"""
Ansible Tower/AWX Orchestration action for the Provision Server lifecycle.

To function, the server you are provisioning must have the following three
parameters set on them:

ansible_tower_id - Must be of type String, Single Select - this expects a
single ID of a Connection Info in CloudBolt with credentials to connect to
Ansible Tower/AWX
ansible_template_ids - Must be of Type String, Single Select - comma
separated list of IDs in Ansible Tower that need to be called
ansible_extra_vars_keys - Must be of type String, Single Select - comma
separated list of the extra vars

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
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def set_token(tower_id):
    global CONN, BASE_URL, HEADERS
    CONN, _ = ConnectionInfo.objects.get_or_create(id=tower_id)
    assert isinstance(CONN, ConnectionInfo)
    BASE_URL = "{}://{}:{}/api/v2".format(CONN.protocol, CONN.ip, CONN.port)
    HEADERS = {'Content-Type': 'application/json',
               'Authorization': f'Bearer {CONN.password}'}


def get_template_details(template_id):
    url = BASE_URL + '/job_templates/' + template_id + '/'
    response = requests.get(url, headers=HEADERS,
                            verify=get_ssl_verification()).json()
    related = response.get('related', None)
    if related:
        related = response['related']
        launch = related.get('launch', None)
    return launch


def get_ansible_extra_vars(server, extra_vars_keys):
    extra_vars = {}
    for key in extra_vars_keys:
        try:
            value = server.get_cfv_for_custom_field(key).value
            extra_vars[key] = value
        except AttributeError:
            logger.warning(f'CustomFieldValue var not found with key: {key}.')
    return str(extra_vars)


def run_template(launch_url, extra_vars):
    url = "{}://{}:{}{}".format(CONN.protocol, CONN.ip, CONN.port, launch_url)
    params = {"extra_vars": {}}
    if extra_vars:
        params['extra_vars'] = extra_vars
    set_progress('Ansible Tower: Launching Job Template')
    set_progress('Ansible Tower: Template Params: {}'.format(params))
    response = requests.post(url, headers=HEADERS, json=params,
                             verify=get_ssl_verification())
    response.raise_for_status()
    status = wait_for_complete(response.json().get('job', None))
    return status


def wait_for_complete(job_template_id):
    url = BASE_URL + '/jobs/{}/'.format(job_template_id)
    status = 'pending'
    while status in ['pending', 'waiting', 'running']:
        response = requests.get(url, headers=HEADERS,
                                verify=get_ssl_verification())
        status = response.json().get('status', None)
        set_progress(f'Ansible Tower: Job ID: {job_template_id} Status: '
                     f'{status}')
        if status in ['successful', 'failed']:
            set_progress(f'response.json(): {response.content}')
            result = response.json().get('result_stdout', None)
            artifacts = response.json()["artifacts"]
            set_progress(result)
            return status, artifacts
        else:
            time.sleep(10)


def write_artifacts_to_server(artifacts, server):
    if artifacts:
        for key in artifacts.keys():
            cf_name = f'awx_{key}'
            cf_value = artifacts[key]
            if type(cf_value) == dict:
                cf_value = json.dumps(cf_value)
                set_progress("cf_value was dict")
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


def get_hostname(server):
    hostname_dict = server.get_cfv_for_custom_field(
        "awx_onefuse_name_machine").value
    hostname = json.loads(hostname_dict)["name"]
    set_progress(f'hostname: {hostname}')
    return hostname


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
        extra_vars_keys = [i.strip() for i in extra_vars_keys.split(",")]
        set_progress(f'extra_vars_keys: {extra_vars_keys}')
        extra_vars = get_ansible_extra_vars(server, extra_vars_keys)
        set_progress(f'extra_vars: {extra_vars}')
        for template_id in template_ids:
            launch_url = get_template_details(template_id=template_id)
            status, artifacts = run_template(launch_url, extra_vars)
            if status != 'successful':
                msg = "Ansible Tower: Job Failed"
                return "FAILURE", "", msg
            else:
                write_artifacts_to_server(artifacts, server)
                hostname = get_hostname(server)
                # May want to look in to setting sc_nic_0_ip here
                return "SUCCESS", hostname, ""
    else:
        return "SUCCESS", "No Server Defined", ""
