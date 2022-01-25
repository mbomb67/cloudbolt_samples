import json
from common.methods import set_progress
from infrastructure.models import CustomField, Namespace, Server
from onefuse.cloudbolt_admin import CbOneFuseManager, Utilities
from utilities.logger import ThreadLogger
from xui.onefuse.globals import (
    MAX_RUNS, IGNORE_PROPERTIES, UPSTREAM_PROPERTY, VERIFY_CERTS
)

logger = ThreadLogger(__name__)


def run(job, **kwargs):
    resource = job.resource_set.first()
    if resource:
        utilities = Utilities(logger)
        create_attributes()
        resource = onefuse_naming(resource, utilities)
        resource = onefuse_ipam(resource, utilities)
        resource = onefuse_dns(resource, utilities)
        if resource.OneFuse_SPS_OS == "OneFuse_SVL_OS_Win":
            onefuse_ad(resource, utilities)
        return "SUCCESS", "", ""
    else:
        set_progress("Resource was not found")


def onefuse_naming(resource, utilities):
    properties_stack = run_property_toolkit(resource, utilities)
    endpoint, policy = get_endpoint_and_policy(properties_stack,
                                               "OneFuse_NamingPolicy")
    set_progress(f"Starting OneFuse Naming Policy: {policy}, Endpoint: {endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(endpoint, VERIFY_CERTS, logger=logger)
    tracking_id = get_tracking_id(properties_stack)
    response_json = ofm.provision_naming(policy, properties_stack, tracking_id)
    resource_name = response_json.get("name")
    resource.name = resource_name
    utilities.check_or_create_cf("OneFuse_Naming")
    response_json["endpoint"] = endpoint
    resource.OneFuse_Naming = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
    resource.set_value_for_custom_field("onefuse_name", resource_name)
    resource.save()
    return resource


def onefuse_ipam(resource, utilities):
    properties_stack = run_property_toolkit(resource, utilities)
    endpoint, policy = get_endpoint_and_policy(properties_stack,
                                               "OneFuse_IpamPolicy_Nic0")
    set_progress(f"Starting OneFuse IPAM Policy: {policy}, Endpoint: {endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(endpoint, VERIFY_CERTS, logger=logger)
    properties_stack = run_property_toolkit(resource, utilities)
    tracking_id = get_tracking_id(properties_stack)
    name = resource.name
    response_json = ofm.provision_ipam(policy, properties_stack, name,
                                       tracking_id)
    utilities.check_or_create_cf("OneFuse_Ipam_Nic0")
    ip_address = response_json["ipAddress"]
    response_json["endpoint"] = endpoint
    resource.OneFuse_Ipam_Nic0 = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
    resource.set_value_for_custom_field("onefuse_ip", ip_address)
    resource.save()
    return resource


def onefuse_dns(resource, utilities):
    properties_stack = run_property_toolkit(resource, utilities)
    endpoint, policy = get_endpoint_and_policy(properties_stack,
                                               "OneFuse_DnsPolicy_Nic0")
    zones_str = properties_stack["OneFuse_DnsPolicy_Nic0"].split(":")[2]
    zones = []
    for zone in zones_str.split(","):
        zones.append(zone.strip())
    set_progress(f"Starting OneFuse DNS Policy: {policy}, Endpoint: {endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(endpoint, VERIFY_CERTS, logger=logger)
    properties_stack = run_property_toolkit(resource, utilities)
    tracking_id = get_tracking_id(properties_stack)
    hostname = properties_stack["onefuse_name"]
    ip_address = properties_stack["onefuse_ip"]
    response_json = ofm.provision_dns(policy, properties_stack,
                                      hostname, ip_address, zones,
                                      tracking_id)
    utilities.check_or_create_cf("OneFuse_Dns_Nic0")
    response_json["endpoint"] = endpoint
    resource.OneFuse_Dns_Nic0 = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
    dns_strings = create_dns_strings(response_json["records"])
    resource.set_value_for_custom_field("onefuse_dns", dns_strings)
    resource.save()
    return resource


def create_dns_strings(records):
    dns_strings = []
    for record in records:
        dns_string = (f'Type: {record[type]}, Name: {record["name"]}, '
                      f'Value: {record["value"]}')
        dns_strings.append(dns_string)
    return dns_strings


def onefuse_ad(resource, utilities):
    properties_stack = run_property_toolkit(resource, utilities)
    endpoint, policy = get_endpoint_and_policy(properties_stack,
                                               "OneFuse_ADPolicy")
    set_progress(f"Starting OneFuse AD Policy: {policy}, Endpoint: {endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(endpoint, VERIFY_CERTS, logger=logger)
    properties_stack = run_property_toolkit(resource, utilities)
    tracking_id = get_tracking_id(properties_stack)
    name = resource.name
    response_json = ofm.provision_ad(policy, properties_stack, name,
                                     tracking_id)
    ad_state = response_json["state"]
    # Move OU
    if ad_state and ad_state.value_as_string == 'build':
        response_json = ofm.move_ou(response_json["id"])
    utilities.check_or_create_cf("OneFuse_AD")
    response_json["endpoint"] = endpoint
    resource.OneFuse_AD = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
    resource.set_value_for_custom_field("onefuse_ou", response_json["finalOu"])
    resource.save()


def create_attributes():
    create_attribute("onefuse_name", "Name")
    create_attribute("onefuse_ip", "IP Address")
    create_attribute("onefuse_dns", "DNS Records", allow_multiple=True)
    create_attribute("onefuse_ou", "Final OU")


def create_attribute(cf_name, cf_label, cf_type="STR", allow_multiple=False):
    namespace, _ = Namespace.objects.get_or_create(name='onefuse_as_a_service')
    defaults = {
        "label": cf_label,
        "allow_multiple": allow_multiple,
        "show_as_attribute": True,
    }

    cf, _ = CustomField.objects.get_or_create(
        name=cf_name, type=cf_type, namespace=namespace, defaults=defaults
    )


def run_property_toolkit(resource, utilities):
    properties_stack = utilities.get_cb_object_properties(resource)
    total_runs = 0
    onefuse_endpoint = "OneFuseProd"
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


def get_tracking_id(properties_stack):
    try:
        tracking_id = properties_stack["OneFuse_Tracking_Id"]
    except KeyError:
        tracking_id = ""
    return tracking_id


def get_endpoint_and_policy(properties_stack, policy_property):
    endpoint = properties_stack[policy_property].split(":")[0]
    policy = properties_stack[policy_property].split(":")[1]
    return endpoint, policy
