import json
from common.methods import set_progress
from onefuse.cloudbolt_admin import CbOneFuseManager, Utilities
from utilities.logger import ThreadLogger
from jobs.models import Job
from xui.onefuse.views import get_onefuse_connection_infos
from xui.onefuse.globals import (
    MAX_RUNS, IGNORE_PROPERTIES, UPSTREAM_PROPERTY, VERIFY_CERTS
)

logger = ThreadLogger(__name__)
ONEFUSE_ENDPOINT = "{{ onefuse_endpoint }}"
NAMING_POLICY = "{{ onefuse_naming_policy }}"


def generate_options_for_onefuse_endpoint(server=None, **kwargs):
    conn_infos = get_onefuse_connection_infos()
    options = []
    for conn_info in conn_infos:
        options.append((conn_info.name, conn_info.name))
    return options

"""
def generate_options_for_onefuse_naming_policy(field, control_value=None, **kwargs):
    if not control_value:
        options = [('', '--- First, Select a Connection Info---')]
        return options
    options = []
    ofm = CbOneFuseManager(control_value)
    return get_naming_policies(ofm)


def get_naming_policies(ofm):
    response = ofm.get("/namingPolicies/")
    response.raise_for_status()
    response_json = response.json()
    policy_names = add_names_from_policies(response_json, [])
    while "next" in response_json["_links"].keys():
        next_url = response_json["_links"]["next"]["href"].replace('/api/v3/onefuse', '')
        response = ofm.get(next_url)
        response.raise_for_status()
        response_json = response.json()
        policy_names = add_names_from_policies(response_json, policy_names)

    return policy_names


def add_names_from_policies(response_json, policy_names):
    naming_policies = response_json["_embedded"]["namingPolicies"]
    for naming_policy in naming_policies:
        policy_names.append((naming_policy["name"], naming_policy["name"]))
    return policy_names
"""

def run(job, **kwargs):
    resource = job.resource_set.first()
    if resource:
        utilities = Utilities(logger)
        logger.debug(f"Provisioning Naming as a Service for resource: "
                     f"{resource}")
        logger.debug(f"Dictionary of keyword args passed to this "
                     f"plug-in: {kwargs.items()}")
        set_progress(f"Starting OneFuse Naming Policy: "
                     f"{NAMING_POLICY}, Endpoint: {ONEFUSE_ENDPOINT}")
        from xui.onefuse.globals import VERIFY_CERTS
        ofm = CbOneFuseManager(ONEFUSE_ENDPOINT, VERIFY_CERTS, logger=logger)
        properties_stack = run_property_toolkit(resource, utilities)
        try:
            tracking_id = properties_stack["OneFuse_Tracking_Id"]
        except KeyError:
            tracking_id = ""
        response_json = ofm.provision_naming(NAMING_POLICY, properties_stack,
                                             tracking_id)
        resource_name = response_json.get("name")
        # resource.name = resource_name
        utilities.check_or_create_cf("OneFuse_Naming")
        utilities.check_or_create_cf("OneFuse_Name")
        response_json["endpoint"] = ONEFUSE_ENDPOINT
        resource.OneFuse_Naming = json.dumps(response_json)
        resource.OneFuse_Name = response_json["name"]
        resource.OneFuse_Tracking_Id = response_json.get("trackingId")
        resource.save()
        return "SUCCESS", resource_name, ""
    else:
        set_progress("Resource was not found")


def run_property_toolkit(resource, utilities):
    properties_stack = utilities.get_cb_object_properties(resource)
    total_runs = 0
    onefuse_endpoint = ONEFUSE_ENDPOINT
    calculated_max_runs = MAX_RUNS
    logger.debug(f'PTK running for Resource: {resource}, max runs set '
                 f'to: {calculated_max_runs}')
    ofm = CbOneFuseManager(onefuse_endpoint, VERIFY_CERTS, logger=logger)
    while total_runs < calculated_max_runs:
        logger.info(f'Starting PTK run #: {total_runs + 1}')
        # OneFuse_SPS groups
        sps_properties = ofm.get_sps_properties(properties_stack,
                                                UPSTREAM_PROPERTY,
                                                IGNORE_PROPERTIES)
        properties_stack = ofm.render_and_apply_properties(sps_properties,
                                                           resource,
                                                           properties_stack)

        # OneFuse_CreateProperties
        create_properties = ofm.get_create_properties(
            properties_stack)
        properties_stack = ofm.render_and_apply_properties(create_properties,
                                                           resource,
                                                           properties_stack)

        properties_stack = utilities.get_cb_object_properties(resource)
        total_runs += 1

    return properties_stack
