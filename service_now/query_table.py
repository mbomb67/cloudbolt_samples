"""
Get Options Action for App ID - a sample for how to query a ServiceNow table
to provide dropdowns for CloudBolt Parameters
"""
from urllib.parse import urlencode

from api.api_samples.python_client.ext import requests
from common.methods import set_progress
from itsm.servicenow.models import ServiceNowITSM
import json
from common.methods import get_proxies


def get_options_list(field, **kwargs):
    options = [('', '--- Select an App ID ---')]
    snowitsm = ServiceNowITSM.objects.first()
    wrapper = snowitsm.get_api_wrapper()
    base_url = wrapper.service_now_instance_url.replace("/login.do", "")
    proxies = get_proxies(base_url)
    req_for_data = lookup_ci(table_name='cmdb_ci_appl',
                             base_url=base_url,
                             conn=snowitsm,
                             sysparm_fields=[
                                 'name',
                                 'u_application_id',
                                 'u_systemid',
                                 'install_status',
                                 'owned_by',
                                 'support_group',
                                 'u_priority',
                                 'version',
                                 'operational_status',
                                 'sys_created_on',
                             ],
                             proxies=proxies,
                             )
    response_json = json.loads(req_for_data)
    results = response_json["result"]
    for result in results:
        if result["support_group"]:
            support_group = result["support_group"]["display_value"]
            result["support_group"] = support_group
        if result["owned_by"]:
            support_group = result["owned_by"]["display_value"]
            result["owned_by"] = support_group
        options.append((json.dumps(result), result["name"]))
    return options


def lookup_ci(table_name=None, base_url=None, conn=None,
              sysparm_fields: list = None, proxies=None, url=None):
    if not url:
        query = ''
        if sysparm_fields:
            query = urlencode({'sysparm_display_value': 'true',
                               'sysparm_fields': ",".join(sysparm_fields)})

        url = base_url + f"/api/now/table/{table_name}?{query}"
    set_progress(f'proxies: {proxies}')
    response = requests.get(
        url=url,
        auth=(conn.service_account, conn.password),
        proxies=proxies,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        verify=False,
        timeout=5.0
    )

    return response.content
