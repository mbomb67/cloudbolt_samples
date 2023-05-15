"""
Ansible Tower Ad-Hoc Job Template
This action allows you to invoke an Ansible Tower Job Template via CloudBolt

1. Allow the selection of an Ansible Tower Connection Info and Job Template in
   the blueprint
2. Capture any Blueprint level Parameters that are prepended with extra_var_
   and pass them in to the Job Template as extra vars
3. Capture any outputs written in Ansible via set_stats or set_facts - write
   these back to the CloudBolt resource as parameters

Setup:
- Connection Info: Create a Connection Info and label it as "ansible_tower".
    - For BASIC Auth scenarios, include the user and password in the Connection
        Info. Be sure that AUTH_TYPE is set to "BASIC" in the script.
    - For OAUTH2 scenarios, include the OAUTH2 token in the Connection Info in
        the password field. Be sure that AUTH_TYPE is set to "OAUTH2" in the
- Ansible template should have the following options enabled:
    Enabled Concurrent Jobs: True (Checked)
    Limit: PROMPT ON LAUNCH = True (Checked)
    Extra Variables: PROMPT ON LAUNCH = True (Checked)
- CloudBolt Blueprint should have this action under the "Build" Tab.
- It is recommended to set default values for each of the following parameters
  under the build tab.
    - ansible_tower: Default value for Ansible Tower
    - ansible_template_id: Default value for Ansible Template ID
    - template_type: Default value for Template Type
- On the blueprint add Parameters for each extra var that needs to be passed
  for the job template. The parameter name should be prepended with extra_var_
"""

from common.methods import set_progress
from utilities.models import ConnectionInfo
from utilities.helpers import get_ssl_verification
from infrastructure.models import Server, CustomField
import requests
import json
import time
import base64
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# Auth Type can be either "OAUTH2" or "BASIC" - BASIC would be used in the case
# Where OAUTH2 Tokens are not allowed - one use case would be for external
# accounts such as LDAP.
AUTH_TYPE = "BASIC"
ANSIBLE_TEMPLATE = "{{ ansible_template_id }}"
CONN_INFO_ID = "{{ ansible_tower }}"
# Template Type is expecting either workflow_job_template or job_template -
# allows for using the same action for either workflow job templates or job
# templates
TEMPLATE_TYPE = "{{ template_type }}"

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


def generate_options_for_ansible_tower(field=None, **kwargs):
    conn_infos = ConnectionInfo.objects.filter(labels__name='ansible_tower')
    return [(ci.id, ci.name) for ci in conn_infos]


def generate_options_for_template_type(field=None, **kwargs):
    return ["job_templates", "workflow_job_templates"]


"""
def generate_options_for_comment_ansible_template_id(field=None,
                                             control_value_dict=None,
                                             **kwargs):
    logger.info(f'control_value: {control_value_dict}')
    if not control_value_dict:
        return [("", "First Select Ansible Tower and Job Type")]
    if not (control_value_dict['ansible_tower'] and
            control_value_dict['template_type']):
        return [("", "First Select Ansible Tower and Job Type")]
    logger.info(f'control_value: {control_value_dict}')
    template_type = control_value_dict['template_type']
    tower_id = control_value_dict['ansible_tower']
    templates = get_tower_templates(template_type, tower_id)
    return [(t['id'], t['name']) for t in templates]
"""


def get_tower_templates(template_type=TEMPLATE_TYPE, tower_id=CONN_INFO_ID):
    base_url = get_base_url(tower_id)
    url = f'{base_url}/{template_type}/'
    logger.info(f'templates_url: {url}')
    response_json = submit_request(url, tower_id=tower_id)
    templates = []
    all_results = response_json.get('results', None)
    next_page = response_json.get("next")
    while next_page:
        next_url = f'{base_url}{next_page.replace("/api/v2", "")}'
        response_json = submit_request(next_url, tower_id=tower_id)
        results = response_json.get('results', None)
        all_results += results
        next_page = response_json.get("next")

    for r in all_results:
        # Include all templates if ASK_LIMIT_ON_LAUNCH is false, if not check
        # the job template for prompt on launch for limit
        if ASK_LIMIT_ON_LAUNCH:
            ask_limit_on_launch = r.get('ask_limit_on_launch', None)
            if not ask_limit_on_launch:
                continue
        templates.append(r)
    logger.info(f'templates: {templates}')
    return templates


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
    set_progress('Ansible Tower: Launching Job Template')
    """
    set_progress('Ansible Tower: Template Params: {}'.format(params))
    set_progress(f'HEADERS: {HEADERS}')
    set_progress(f'url: {url}')
    """
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
    if TEMPLATE_TYPE != "workflow_job_templates" and \
            TEMPLATE_TYPE != "job_templates":
        raise Exception(f"TEMPLATE_TYPE is not equal to either "
                        f"workflow_job_template, or job_template.")

    if not TEMPLATE_TYPE:
        raise Exception(f"TEMPLATE_TYPE is not set for plugin")
    launch_url = get_template_details(template_id=ANSIBLE_TEMPLATE)
    status, response_json = run_template(launch_url, resource)
    write_artifacts_to_resource(response_json, resource)

    if status != 'successful':
        msg = "Ansible Tower: Job Failed"
        return "FAILURE", "", msg
    else:
        return "SUCCESS", "", ""