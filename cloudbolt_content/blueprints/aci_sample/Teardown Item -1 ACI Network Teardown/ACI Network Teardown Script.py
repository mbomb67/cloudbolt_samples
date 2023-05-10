"""
This is a very basic example of how you could teardown an object via REST API.
This plugin is counting that the resource was built using the aci_build.py
module and that the resource in question has values set for the "aci_conn_info"
and "aci_network_id" parameters (called CustomField in the DB).

This module will:
1. Use the run insertion point for the teardown action
2. Gather the "aci_conn_info" and "aci_network_id" values set from build
3. Show a sample of how to potentially delete an object via REST
"""
from common.methods import set_progress
from utilities.models import ConnectionInfo


def run(job, resource: None, **kwargs):
    if resource:
        try:
            delete_aci_network(resource)
            return "SUCCESS", "", ""
        except Exception as err:
            return "FAILURE", err, ""
    else:
        return "FAILURE", "Resource not found", ""


def delete_aci_network(resource):
    conn_info_id = resource.get_cfv_for_custom_field("aci_conn_info").value
    network_id = resource.get_cfv_for_custom_field("aci_network_id").value
    set_progress(f'Deleting Network ID: {network_id} from ')
    base_url, username, password = get_conn_info_data(conn_info_id)
    set_progress(f'Submitting request against: {base_url}')
    """
    This action could look something more like the following, note it is using 
    the global parameters set for SNM, gateway and network to pass in to the 
    post function. Instead of basic auth, you could also use token based auth. 
    You can also construct this to handle async REST calls to check the status 
    of a request until it is complete: 
    
    import requests
    from requests.auth import HTTPBasicAuth
    path = f'/api/v2/networks/{network_id}/'
    verify_certs = True
    response = requests.delete(
        f'{base_url}{path},
        auth=HTTPBasicAuth(username, password),
        verify=verify_certs
    )
    response.raise_for_status()
    """


def get_conn_info_data(conn_info_id):
    ci = ConnectionInfo.objects.get(id=conn_info_id)
    username = ci.username
    password = ci.password
    base_url = f'{ci.protocol}://{ci.ip}'
    return base_url, username, password
