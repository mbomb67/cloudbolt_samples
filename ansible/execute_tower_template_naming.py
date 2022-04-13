"""
Ansible Tower/AWX Orchestration action for the Provision Server lifecycle.

To function, the server you are provisioning must have the following three
parameters set on them:

ansible_tower_id - Must be of type String, Single Select - this expects a
single ID of a Connection Info in CloudBolt with credentials to connect to
Ansible Tower/AWX

ansible_template_ids - Must be of Type String, Single Select - comma
separated list of IDs in Ansible Tower that need to be called

ansible_extra_vars_keys - Must be of type String (or Code), Single Select.
A json formatted object mapping CB parameters to expected Ansible extra vars
Ex: {"os_family.parent.name":"platform", "dns_domain": "dnsdomain",
"network_cidr", "network_cidr"}
NOTE: The network_cidr key will calculate the CIDR based off of the CB network
selected for the server

Ensure that parameters that match your ansible_extra_vars_keys are getting set
on the server.
"""

from common.methods import set_progress
from utilities.models import ConnectionInfo
from utilities.helpers import get_ssl_verification
from infrastructure.models import Server, CustomField
import requests
import json
import time
import ipaddress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def set_token(tower_id):
    global CONN, BASE_URL, HEADERS
    CONN, _ = ConnectionInfo.objects.get_or_create(id=tower_id)
    assert isinstance(CONN, ConnectionInfo)
    BASE_URL = f'{CONN.protocol}://{CONN.ip}:{CONN.port}'
    HEADERS = {'Content-Type': 'application/json',
               'Authorization': f'Bearer {CONN.password}'}


def get_template_details(template_id):
    url = f'{BASE_URL}/api/v2/job_templates/{template_id}/'
    response = requests.get(url, headers=HEADERS,
                            verify=get_ssl_verification()).json()
    related = response.get('related', None)
    if related:
        related = response['related']
        launch = related.get('launch', None)
    return launch


def get_ansible_extra_vars(server, extra_vars_keys):
    mappings = json.loads(extra_vars_keys)
    extra_vars = {}
    for key in mappings.keys():
        if key == "network_cidr":
            net = server.sc_nic_0
            gw = net.gateway
            snm = net.netmask
            value = ipaddress.ip_network(f'{gw}/{snm}', strict=False)
            logger.info(f'network_cidr: {value} gw: {gw}, snm: {snm}')
        else:
            try:
                value = server.get_cfv_for_custom_field(key).value
            except AttributeError:
                try:
                    # If it makes it here the key may not be a CustomField, try an eval
                    eval_str = 'server'
                    for i in key.split("."):
                        eval_str = f'{eval_str}.{i}'
                    value = eval(eval_str)
                except AttributeError:
                    logger.warning(f'Value var not found with key: {key}.')
                    continue
        if value:
            extra_vars[mappings[key]] = value
    return str(extra_vars)


def run_template(launch_url, extra_vars):
    url = "{}://{}:{}{}".format(CONN.protocol, CONN.ip, CONN.port, launch_url)
    params = {"extra_vars": {}}
    if extra_vars:
        params['extra_vars'] = extra_vars
    set_progress('Ansible Tower: Launching Job Template')
    set_progress(f'Ansible Tower: Template Params: {params}')
    response = requests.post(url, headers=HEADERS, json=params,
                             verify=get_ssl_verification())
    response.raise_for_status()
    status, response_json = wait_for_complete(response.json().get('job', None))
    return status, response_json


def wait_for_complete(job_template_id):
    url = f'{BASE_URL}/api/v2/jobs/{job_template_id}/'
    status = 'pending'
    while status in ['pending', 'waiting', 'running']:
        response = requests.get(url, headers=HEADERS,
                                verify=get_ssl_verification())
        status = response.json().get('status', None)
        set_progress(f'Ansible Tower: Job ID: {job_template_id} Status: '
                     f'{status}')
        if status in ['successful', 'failed']:
            logger.debug(f'response.json(): {response.content}')
            result = response.json().get('result_stdout', None)
            artifacts = response.json()["artifacts"]
            logger.debug(result)
            return status, response.json()
        else:
            time.sleep(10)


def write_artifacts_to_server(response_json, server):
    artifacts = response_json["artifacts"]
    job_events_facts = get_job_events_facts(response_json)
    facts = {**artifacts, **job_events_facts}
    if facts:
        logger.info(f'Writing facts to Server. Facts: {facts}')
        for key in facts.keys():
            cf_name = f'awx_{key}'
            cf_value = facts[key]
            if type(cf_value) == dict:
                cf_value = json.dumps(cf_value)
                logger.info("cf_value for {} was dict")
            description = "Created by Ansible Artifacts"
            defaults = {
                "label": key,
                "description": description,
                "show_on_servers": True
            }
            cf, _ = CustomField.objects.get_or_create(
                name=cf_name, type="STR", defaults=defaults
            )
            server.set_value_for_custom_field(cf_name, cf_value)


def get_job_events_facts(response_json):
    try:
        events_page = response_json["related"]["job_events"]
        events_url = f'{BASE_URL}{events_page}'
    except KeyError:
        return None
    facts = {}
    response = requests.get(events_url, headers=HEADERS,
                            verify=get_ssl_verification())
    response.raise_for_status()
    facts = add_facts_to_list(response.json(), facts)
    next_page = response.json()["next"]
    while next_page:
        next_url = f'{BASE_URL}{next_page}'
        response = requests.get(next_url, headers=HEADERS,
                                verify=get_ssl_verification())
        response.raise_for_status()
        facts = add_facts_to_list(response.json(), facts)
        next_page = response.json()["next"]
    return facts


def add_facts_to_list(response_json, facts):
    for result in response_json["results"]:
        try:
            if result["event"] == "runner_on_ok":
                event_data = result["event_data"]
                if event_data["task_action"] == "set_fact":
                    ansible_facts = event_data["res"]["ansible_facts"]
                    for key in ansible_facts.keys():
                        facts[key] = ansible_facts[key]
        except KeyError as e:
            logger.warning(f'Error encountered gathering facts. Error: {e}')
    return facts


def run(job, **kwargs):
    server = job.server_set.first()
    if server:
        try:
            tower_id = server.get_cfv_for_custom_field(
                "ansible_tower_id").value
            template_ids = server.get_cfv_for_custom_field(
                "ansible_template_ids").value
            extra_vars_keys = server.get_cfv_for_custom_field(
                "ansible_extra_vars_keys").value
        except AttributeError as e:
            msg = f'Ansible inputs not set for server, skipping execution.'
            return "SUCCESS", msg, ""
        set_token(tower_id)
        template_ids = [i.strip() for i in template_ids.split(",")]
        set_progress(f'template_ids: {template_ids}')
        extra_vars = get_ansible_extra_vars(server, extra_vars_keys)
        set_progress(f'extra_vars: {extra_vars}')
        for template_id in template_ids:
            launch_url = get_template_details(template_id=template_id)
            status, response_json = run_template(launch_url, extra_vars)
            if status != 'successful':
                msg = "Ansible Tower: Job Failed"
                return "FAILURE", "", msg
            else:
                write_artifacts_to_server(response_json, server)
                hostname = server.get_cfv_for_custom_field(
                    "awx_ansible_hostname").value
                server.sc_nic_0_ip = server.get_cfv_for_custom_field(
                    "awx_ipaddr").value
                server.save()
                # May want to look in to setting sc_nic_0_ip here
                return "SUCCESS", hostname, ""
    else:
        return "SUCCESS", "No Server Defined", ""