"""
This is a very basic example of how you could leverage Connection Infos in
CloudBolt to connect to Cisco ACI (or literally anything else). There is no
functional code here, the framework will need to be filled in.

Pre-requisite - create a connection info (Admin > Connection Info) in CloudBolt
That is labeled with "aci"

Documentation and further examples can be found below:
https://docs.cloudbolt.io/articles/#!cloudbolt-latest-docs/advanced-option-returns
https://docs.cloudbolt.io/articles/#!cloudbolt-latest-docs/resources-for-writing-plug-ins
https://docs.cloudbolt.io/articles/#!cloudbolt-latest-docs/plug-in-parameterization
https://docs.cloudbolt.io/articles/#!cloudbolt-latest-docs/plug-in-examples

And from the Django documentation:
https://docs.djangoproject.com/en/4.0/ref/models/querysets/

This plugin shows how to:
1. Pass a variable (aci) in to a CloudBolt Plugin
2. Use generated options to create a dropdown list of Connection Infos labeled
   "aci"
3. Use the run method as the insertion point to this plugin in a CloudBolt
   Blueprint
4. Gather URL, username and password from a Connection Info
5. Show how to do something, and write parameters back to the created resource
6. These parameters can then be used to delete the things that were created
   from the blueprint
"""
from common.methods import set_progress
from infrastructure.models import CustomField
from utilities.models import ConnectionInfo

CONN_INFO_ID = "{{aci}}"
SUBNET_MASK = "{{subnet_mask}}"
SUBNET_NETWORK = "{{subnet_network}}"
SUBNET_GATEWAY = "{{subnet_gateway}}"
CONN_INFO_LABEL = "aci"


def generate_options_for_aci(server=None, **kwargs):
    conn_infos = ConnectionInfo.objects.filter(labels__name=CONN_INFO_LABEL)
    options = [(ci.id, ci.name) for ci in conn_infos]
    if not options:
        options = [('', '------No Connection Infos Found with Label "aci"------')]
    return options


def run(job, resource: None, **kwargs):
    if resource:
        try:
            set_progress(f'kwargs: {kwargs}')
            network_id = create_network()
            save_info_to_resource(resource, network_id)
            return "SUCCESS", "", ""
        except Exception as err:
            return "FAILURE", err, ""
    else:
        return "FAILURE", "Resource not found", ""


def create_network():
    base_url, username, password = get_conn_info_data()
    set_progress(f'Submitting request against: {base_url}')
    """
    This action could look something more like the following, note it is using 
    the global parameters set for SNM, gateway and network to pass in to the 
    post function. Instead of basic auth, you could also use token based auth. 
    You can also construct this to handle async REST calls to check the status 
    of a request until it is complete: 
    
    import requests
    from requests.auth import HTTPBasicAuth
    path = '/api/v2/createNetwork/'
    verify_certs = True
    json_payload = {
        "subnet_mask": SUBNET_MASK,
        "network": SUBNET_NETWORK,
        "gateway": SUBNET_GATEWAY,
    }
    response = requests.post(
        f'{base_url}{path},
        auth=HTTPBasicAuth(username, password),
        verify=verify_certs,
        json=json_payload
    )
    response.raise_for_status()
    response_json = response.json()
    network_id = response_json["id"]
    """
    network_id = "1"
    return network_id


def save_info_to_resource(resource, network_id):
    """
    Save the Connection Info ID and the created Resource ID to the Resource in
    CloudBolt. If more than one element is created, you could save multiple
    IDs and other metadata to the Resource.
    """
    cf = create_custom_field("aci_conn_info", "ACI Connection Info", "STR",
                             "Connection Info used to connect to ACI")
    resource.set_value_for_custom_field(cf.name, CONN_INFO_ID)
    cf = create_custom_field("aci_network_id", "ACI Network ID", "STR",
                             "ID for the created ACI network")
    resource.set_value_for_custom_field(cf.name, network_id)


def create_custom_field(cf_name, cf_label, cf_type, description,
                        required = False, allow_multiple = False, **kwargs):
    defaults = {
        "label": cf_label,
        "description": description,
        "required": required,
        "allow_multiple": allow_multiple,
    }
    for key, value in kwargs.items():
        defaults[key] = value

    cf, _ = CustomField.objects.get_or_create(
        name=cf_name, type=cf_type, defaults=defaults
    )
    return cf


def get_conn_info_data():
    ci = ConnectionInfo.objects.get(id=CONN_INFO_ID)
    username = ci.username
    password = ci.password
    base_url = f'{ci.protocol}://{ci.ip}'
    if ci.port:
        base_url = f'{base_url}:{ci.port}'
    return base_url, username, password
