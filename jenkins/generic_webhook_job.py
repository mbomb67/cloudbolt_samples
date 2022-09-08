"""
Invoke a Jenkins job via the Generic Webhook Trigger Plugin.
Generic Webhook Plugin docs:
https://plugins.jenkins.io/generic-webhook-trigger/
"""

import requests
import json
import time

from c2_wrapper import create_custom_field
from common.methods import set_progress
from requests.auth import HTTPBasicAuth

from infrastructure.models import Environment, Server
from resourcehandlers.vmware.models import VsphereResourceHandler
from utilities.logger import ThreadLogger
from jobs.models import Job

logger = ThreadLogger(__name__)


def generate_options_for_os(**kwargs):
    return ["CloudBolt_Rocky_8"]


def generate_options_for_size(**kwargs):
    return ["small", "medium", "large", "xlarge"]


def generate_options_for_site(**kwargs):
    return ["itwpoc"]


def generate_options_for_domain(field, control_value=None, **kwargs):
    if not control_value:
        return [("", "------First Select a Site------")]
    if control_value == "iteprod" or control_value == "apsprod":
        return [("cld.uspto.gov", "cld.uspto.gov")]
    else:
        return [
            ("dev.uspto.gov", "dev.uspto.gov"),
            ("test.uspto.gov", "test.uspto.gov"),
        ]


def generate_options_for_business_center(**kwargs):
    return ["", "corp", "pat", "tm", "fin"]


def generate_options_for_count(field, control_value=None, **kwargs):
    if not control_value:
        return [("", "------First Select a Size------")]
    if control_value == "small" or control_value == "medium":
        return [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), (6, 6), (7, 7), (8, 8),
                (9, 9), (10, 10), ("na", "na")]
    elif control_value == "large":
        return [(1, 1), (2, 2), (3, 3), (4, 4), (5, 5), ("na", "na")]
    elif control_value == "xlarge":
        return [(1, 1), (2, 2), ("na", "na")]


def run(job, resource=None, **kwargs):
    ## Templated Inputs
    blueprint_context = kwargs.get("blueprint_context")
    logger.debug(f"Blueprint Context: {blueprint_context}")
    save_inputs_to_resource(blueprint_context, resource)
    fqdn = "{{jenkins_fqdn}}"
    protocol = "{{protocol}}"
    webhook_token = "{{webhook_token}}"
    pipeline_name = "{{pipeline_name}}"
    username = "{{jenkins_username}}"
    token = "{{jenkins_token}}"
    business_center = "{{business_center}}"
    domain = "{{domain}}"
    index = "{{index}}"
    application_name = "{{application_name}}"
    layer = "{{layer}}"
    product_name = "{{product_name}}"
    name = f'{application_name}-{layer}-{product_name}'
    count = "{{count}}"
    payload = {
        "name": name,
        "os": "{{os}}",
        "size": "{{size}}",
        "domain": domain,
        "site": "{{site}}",
        "index": index,
        "count": count,
        "tags": "{{tags}}"
    }
    if business_center:
        payload["business_center"] = business_center
    base_url = f'{protocol}://{fqdn}'
    url = f'{base_url}/generic-webhook-trigger/invoke?token={webhook_token}'
    logger.info(f'URL: {url}, Payload: {json.dumps(payload)}')
    json_data = json.dumps(payload)
    headers = {"Content-Type": "application/json"}
    response = requests.post(url=url, data=json_data, headers=headers,
                             verify=False)
    response.raise_for_status()
    build_json = wait_for_jenkins_job(response, pipeline_name, fqdn, protocol,
                                      username, token, headers)
    server = register_server_objects(name, domain, index, count, resource,
                                     job)
    return "", "", ""


def save_inputs_to_resource(blueprint_context, resource):
    job_data = blueprint_context["jenkins_generic_webhook_job"]
    for key in job_data.keys():
        if key != "__untrusted_expressions" and key != "service_item_id":
            name = f'jenkins_bp_{key}'
            field = create_custom_field(name, key, "STR", show_on_servers=True,
                                        namespace="jenkins_bp")
            resource.set_value_for_custom_field(name, job_data[key])


def wait_for_jenkins_job(response, pipeline_name, fqdn, protocol, username,
                         token, headers):
    logger.info(f'response: {response.content}')
    queue_url = response.json()['jobs'][pipeline_name]['url']
    auth = HTTPBasicAuth(username, token)
    build_url = get_build_url(protocol, fqdn, queue_url, headers, auth)
    build_json = get_build_json(build_url, headers, auth)
    return build_json


def get_build_url(protocol, fqdn, queue_url, headers, auth):
    url = f'{protocol}://{fqdn}/{queue_url}api/json'
    build_url = ""
    max_sleep = 500
    total_sleep = 0
    sleep_time = 5
    while not build_url:
        response = requests.post(url=url, headers=headers, auth=auth,
                                 verify=False)
        response.raise_for_status()
        logger.debug(f'Response Content: {response.content}')
        try:
            build_url = response.json()["executable"]["url"]
        except Exception:
            logger.info("Build URL not available, sleeping.")
        time.sleep(sleep_time)
        total_sleep += sleep_time
        if total_sleep > max_sleep:
            raise Exception("Max sleep exceeded waiting for Build URL")
    build_url = f'{build_url}api/json'
    logger.info(f'build_url: {build_url}')
    return build_url


def get_build_json(build_url, headers, auth):
    building = True
    max_sleep = 1000
    total_sleep = 0
    sleep_time = 30
    while building:
        build_response = requests.post(url=build_url, headers=headers,
                                       auth=auth, verify=False)
        build_response.raise_for_status()
        logger.debug(f'content: {build_response.content}')
        build_json = build_response.json()
        building = build_json["building"]
        logger.info(f'Job building: {building}')
        time.sleep(sleep_time)
        total_sleep += sleep_time
        if total_sleep > max_sleep:
            raise Exception("Max sleep exceeded waiting for Build URL")
        if building:
            logger.info(f"Jenkins Job is still building sleeping for "
                        f"{sleep_time} seconds.")
    result = build_json["result"]
    if result != "SUCCESS":
        raise Exception(f"Build result is not SUCCESS. Actual result: "
                        f"{result}")
    return build_json


def register_server_objects(prefix, domain, index, count, resource, job):
    if count == "na":
        hostname = prefix
        if domain:
            fqdn = f'{prefix}.{domain}'
        else:
            fqdn = f'{prefix}'
        try:
            register_server_object(hostname, fqdn, resource, job)
        except Exception as e:
            logger.warning(f"Unable to register server with hostname: "
                           f"{hostname}, Error: {e}")
    else:
        total_servers = int(count)
        server_count = 0
        while server_count < total_servers:
            try:
                hostname, fqdn = determine_hostname_fqdn(prefix, index, domain,
                                                         server_count)
                register_server_object(hostname, fqdn, resource, job)
            except Exception as e:
                logger.warning(f"Unable to register server with hostname: "
                               f"{hostname}, Error: {e}")
            server_count += 1


def determine_hostname_fqdn(prefix, index, domain, server_count):
    current_server_index = int(index) + int(server_count)
    hostname = f'{prefix}-{current_server_index}'
    if domain:
        fqdn = f'{hostname}.{domain}'
    else:
        fqdn = f'{hostname}'

    return hostname, fqdn


def register_server_object(hostname, fqdn, resource, job):
    vm, rh = get_vcenter_vm_by_fqdn(fqdn)
    moid = str(vm).replace("'", "").split(':')[1]
    uuid = vm.config.uuid
    cluster = vm.runtime.host.parent.name
    tech_dict = {
        "moid": moid,
        "cluster": cluster,
    }
    env = Environment.objects.filter(resource_handler_id=rh.id,
                                     vmware_cluster=cluster).first()
    if not env:
        raise Exception(f'Environment could not be found with rh: {rh}, and '
                        f'cluster: {cluster}')
    group = resource.group
    owner = resource.owner
    try:
        server = Server.objects.get(resource_handler_svr_id=uuid)
        server.resource = resource
        server.group = group
        server.owner = owner
        server.environment = env
        server.save()
        logger.info(f"Found existing server record: '{server}'")
    except Server.DoesNotExist:
        logger.info(
            f"Creating new server with resource_handler_svr_id "
            f"'{moid}', resource '{resource}', group '{group}', "
            f"owner '{owner}', and "
            f"environment '{env}, and rh: {rh}'"
        )
        server = Server(
            hostname=hostname,
            resource_handler_svr_id=uuid,
            resource=resource,
            group=group,
            owner=owner,
            environment=env,
        )
        server.save()
        server.resource_handler = rh
        server.save()
    try:
        rh.cast().update_tech_specific_server_details(server, tech_dict)
        server.refresh_info()
    except Exception as err:
        logger.info(f"tech_dict: {tech_dict}")
        logger.warning(f'Unable to directly sync server, verify '
                       f'that the chosen region/vpc has been '
                       f'imported to CloudBolt. Error: {err}')
    job.server_set.add(server)
    return server


def get_vcenter_vm_by_fqdn(fqdn):
    all_vcenters = VsphereResourceHandler.objects.all()
    for vc in all_vcenters:
        wrapper = vc.get_api_wrapper()
        si = wrapper._get_connection()
        vm = si.content.searchIndex.FindByDnsName(None, fqdn, True)
        if vm:
            logger.info(f"VM Found with name: {vm.name}")
            rh = vc.resourcehandler_ptr
            return vm, rh

    raise Exception(f"VM not found with Dns Name: {fqdn}")
