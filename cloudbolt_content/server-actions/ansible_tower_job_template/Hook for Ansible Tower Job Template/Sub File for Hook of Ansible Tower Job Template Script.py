"""
Ansible Tower Day-2 Ad-Hoc Job Template
This action allows you to invoke an Ansible Tower Job Template via a CloudBolt
Server Action. This action is intended to be used as a Day-2 action, meaning
that it is intended to be used after the initial provisioning of a resource.

1. Execute a job template against an Ansible Tower instance
2. Pass in the hostname as FQDN to the limit parameter

Setup:
- Connection Info: Create a Connection Info and label it as "ansible_tower".
    - For BASIC Auth scenarios, include the user and password in the Connection
        Info. Be sure that AUTH_TYPE is set to "BASIC" in the script.
    - For OAUTH2 scenarios, include the OAUTH2 token in the Connection Info in
        the password field. Be sure that AUTH_TYPE is set to "OAUTH2" in the
    - Capture the ID of the Connection Info and set it as CONN_INFO_ID in the
        script.
- Ansible template should have the following options enabled:
    Enabled Concurrent Jobs: True (Checked)
    Limit: PROMPT ON LAUNCH = True (Checked)
    Extra Variables: PROMPT ON LAUNCH = True (Checked)
- This action should be set as a Server Action in CloudBolt
"""

from common.methods import set_progress
from utilities.models import ConnectionInfo
from utilities.helpers import get_ssl_verification
from infrastructure.models import Server, CustomField
import requests
import json
import time
import base64
from xui.onefuse.shared import utilities
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# Auth Type can be either "OAUTH2" or "BASIC" - BASIC would be used in the case
# Where OAUTH2 Tokens are not allowed - one use case would be for external
# accounts such as LDAP.
AUTH_TYPE = "BASIC"
ANSIBLE_TEMPLATE = "80"
CONN_INFO_ID = "14"
# Template Type is expecting either workflow_job_templates or job_templates -
# allows for using the same action for either workflow job templates or job
# templates
TEMPLATE_TYPE = "job_templates"

ASK_LIMIT_ON_LAUNCH = False


def get_headers(tower_id=CONN_INFO_ID):
    conn = get_connection_info(tower_id)
    if AUTH_TYPE == "OAUTH2":
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'Bearer {conn.password}'}
    elif AUTH_TYPE == "BASIC":
        creds = f'{conn.username}:{conn.password}'
        base_64 = base64.b64encode(f'{creds}'.encode("ascii")).decode('ascii')
        headers = {'Content-Type': 'application/json',
                   'Authorization': f'Basic {base_64}'}
    else:
        raise Exception("AUTH_TYPE must be either OAUTH2 or BASIC")
    return headers


def get_base_url(tower_id=CONN_INFO_ID):
    conn = get_connection_info(tower_id)
    base_url = f'{conn.protocol}://{conn.ip}'
    if conn.port:
        base_url += f':{conn.port}'
    base_url += '/api/v2'
    return base_url


def get_connection_info(tower_id=CONN_INFO_ID):
    conn = ConnectionInfo.objects.get(id=tower_id)
    assert isinstance(conn, ConnectionInfo)
    return conn


"""
Disabling generated options until hide when default value is set is fixed
def disabled generate_options_for_ansible_tower(field=None, **kwargs):
    conn_infos = ConnectionInfo.objects.filter(labels__name='ansible_tower')
    return [(ci.id, ci.name) for ci in conn_infos]


def disabled generate_options_for_template_type(field=None, **kwargs):
    return ["job_templates", "workflow_job_templates"]
"""


def get_template_details(template_id):
    base_url = get_base_url()
    url = f'{base_url}/{TEMPLATE_TYPE}/{template_id}/'
    response_json = submit_request(url)
    logger.info(f'response_json: {response_json}')
    related = response_json.get('related', None)
    if related:
        related = response_json['related']
        launch = related.get('launch', None)
    return launch


def submit_request(url: str, method: str = "get", tower_id=CONN_INFO_ID,
                   **kwargs):
    headers = get_headers(tower_id)
    # set_progress(f'Headers: {headers}')
    if method == "get":
        response = requests.get(url, headers=headers,
                                verify=get_ssl_verification())
    if method == 'post':
        response = requests.post(url, headers=headers,
                                 verify=get_ssl_verification(), **kwargs)
    try:
        response.raise_for_status()
    except Exception as e:
        logger.error(f'Error encountered for URL: {url}, details: '
                     f'{e.response.content}')
        raise
    return response.json()


def get_ansible_extra_vars(resource):
    extra_vars = {}
    for key, value in resource.get_cf_values_as_dict().items():
        if key.find('extra_var_') == 0:
            stripped_key = key.replace('extra_var_', '')
            extra_vars[stripped_key] = value
    return str(extra_vars)


def get_limit(server):
    network_info = utilities.get_network_info(server)
    fqdn = network_info['OneFuse_VmNic0']['fqdn']
    return fqdn


def run_template(launch_url, resource, tower_id=CONN_INFO_ID):
    conn = get_connection_info(tower_id)
    url = f"{conn.protocol}://{conn.ip}"
    if conn.port:
        url += f':{conn.port}'
    url += f'{launch_url}'
    extra_vars = get_ansible_extra_vars(resource)
    params = {"extra_vars": ""}
    if extra_vars:
        params['extra_vars'] = extra_vars
    limit = get_limit(resource)
    if limit:
        params['limit'] = limit
    set_progress(f'Ansible Tower: Launching Job Template: {url}')
    set_progress('Ansible Tower: Template Params: {}'.format(params))
    # set_progress(f'HEADERS: {HEADERS}')
    set_progress(f'url: {url}')
    response_json = submit_request(url, "post", json=params)
    if TEMPLATE_TYPE == "workflow_job_template":
        job_type = 'workflow_job'
    else:
        job_type = 'job'
    job_id = response_json[job_type]
    status, response_json = wait_for_complete(job_id, job_type)
    return status, response_json


def wait_for_complete(job_id, job_type):
    base_url = get_base_url()
    url = f'{base_url}/{job_type}s/{job_id}/'
    status = 'pending'
    while status in ['pending', 'waiting', 'running']:
        response_json = submit_request(url)
        status = response_json.get('status', None)
        set_progress(f'Ansible Tower: Job ID: {job_id} Status: {status}')
        if status in ['successful', 'failed']:
            # logger.info(f'response_json: {json.dumps(response.json())}')
            result = response_json.get('result_stdout', None)
            set_progress(result)
            return status, response_json
        else:
            time.sleep(10)


def write_artifacts_to_resource(response_json, resource):
    base_url = get_base_url()
    if TEMPLATE_TYPE == "workflow_job_template":
        nodes = response_json["related"]["workflow_nodes"].replace('/api/v2', '')
        nodes_url = f'{base_url}{nodes}'
        response_json = submit_request(nodes_url)
        for result in response_json["results"]:
            job = result["related"]["job"].replace('/api/v2', '')
            job_url = f'{base_url}{job}'
            job_json = submit_request(job_url)
            write_job_artifacts_to_resource(job_json, resource)
    else:
        write_job_artifacts_to_resource(response_json, resource)


def write_job_artifacts_to_resource(job_json, resource):
    artifacts = job_json["artifacts"]
    job_events_facts = get_job_events_facts(job_json)
    facts = {**artifacts, **job_events_facts}
    if facts:
        logger.info(f'Writing facts to Server. Facts: {facts}')
        for key in facts.keys():
            cf_name = f'awx_{key}'
            cf_value = facts[key]
            if type(cf_value) == dict:
                cf_value = json.dumps(cf_value)
                logger.info(f"cf_value for {key} was dict")
            description = "Created by Ansible Artifacts"
            defaults = {
                "label": key,
                "description": description,
                "show_on_servers": True
            }
            cf, _ = CustomField.objects.get_or_create(
                name=cf_name, type="STR", defaults=defaults
            )
            resource.set_value_for_custom_field(cf_name, cf_value)


def get_job_events_facts(response_json):
    base_url = get_base_url()
    try:
        events_page = response_json["related"]["job_events"]
        events_url = f'{base_url}{events_page.replace("/api/v2", "")}'
    except KeyError:
        return None
    facts = {}
    response_json = submit_request(events_url)
    facts = add_facts_to_list(response_json, facts)
    next_page = response_json["next"]
    while next_page:
        next_url = f'{base_url}{next_page}'
        response_json = submit_request(next_url)
        facts = add_facts_to_list(response_json, facts)
        next_page = response_json["next"]
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


def run(job=None, logger=None, server=None, resource=None, **kwargs):
    logger.debug(f'TEMPLATE_TYPE: {TEMPLATE_TYPE}')
    if TEMPLATE_TYPE != "workflow_job_templates" and \
            TEMPLATE_TYPE != "job_templates":
        raise Exception(f"TEMPLATE_TYPE is not equal to either "
                        f"workflow_job_template, or job_template.")

    if not TEMPLATE_TYPE:
        raise Exception(f"TEMPLATE_TYPE is not set for plugin")
    launch_url = get_template_details(template_id=ANSIBLE_TEMPLATE)
    status, response_json = run_template(launch_url, server)
    write_artifacts_to_resource(response_json, resource)

    if status != 'successful':
        msg = "Ansible Tower: Job Failed"
        return "FAILURE", "", msg
    else:
        return "SUCCESS", "", ""