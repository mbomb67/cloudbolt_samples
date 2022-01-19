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
    logger.debug(f"Provisioning Naming as a Service for resource: "
                 f"{resource}")
    properties_stack = run_property_toolkit(resource, utilities)
    onefuse_endpoint = properties_stack["OneFuse_NamingPolicy"].split(":")[0]
    naming_policy = properties_stack["OneFuse_NamingPolicy"].split(":")[1]
    set_progress(f"Starting OneFuse Naming Policy: "
                 f"{naming_policy}, Endpoint: {onefuse_endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(onefuse_endpoint, VERIFY_CERTS, logger=logger)
    try:
        tracking_id = properties_stack["OneFuse_Tracking_Id"]
    except KeyError:
        tracking_id = ""
    response_json = ofm.provision_naming(naming_policy, properties_stack,
                                         tracking_id)
    resource_name = response_json.get("name")
    resource.name = resource_name
    utilities.check_or_create_cf("OneFuse_Naming")
    response_json["endpoint"] = onefuse_endpoint
    resource.OneFuse_Naming = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
    resource.set_value_for_custom_field("onefuse_name", resource_name)
    resource.save()


def onefuse_ipam(resource, utilities):
    logger.debug(f"Provisioning IPAM as a Service for resource: "
                 f"{resource}")
    properties_stack = run_property_toolkit(resource, utilities)
    onefuse_endpoint = properties_stack["OneFuse_IpamPolicy_Nic0"].split(":")[0]
    ipam_policy = properties_stack["OneFuse_IpamPolicy_Nic0"].split(":")[1]
    set_progress(f"Starting OneFuse Naming Policy: "
                 f"{ipam_policy}, Endpoint: {onefuse_endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(onefuse_endpoint, VERIFY_CERTS, logger=logger)
    properties_stack = run_property_toolkit(resource, utilities)
    try:
        tracking_id = properties_stack["OneFuse_Tracking_Id"]
    except KeyError:
        tracking_id = ""
    name = resource.name
    response_json = ofm.provision_ipam(ipam_policy, properties_stack, name,
                                       tracking_id)
    utilities.check_or_create_cf("OneFuse_Ipam_Nic0")
    ip_address = response_json["ip_address"]
    response_json["endpoint"] = onefuse_endpoint
    resource.OneFuse_Ipam_Nic0 = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
    resource.set_value_for_custom_field("onefuse_ip", ip_address)
    resource.save()


def onefuse_dns(resource, utilities):
    logger.debug(f"Provisioning DNS as a Service for resource: "
                 f"{resource}")
    properties_stack = run_property_toolkit(resource, utilities)
    onefuse_endpoint = properties_stack["OneFuse_DnsPolicy_Nic0"].split(":")[0]
    dns_policy = properties_stack["OneFuse_DnsPolicy_Nic0"].split(":")[1]
    zones_str = properties_stack["OneFuse_DnsPolicy_Nic0"].split(":")[2]
    zones = []
    for zone in zones_str.split(","):
        zones.append(zone.strip())
    set_progress(f"Starting OneFuse Naming Policy: "
                 f"{dns_policy}, Endpoint: {onefuse_endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(onefuse_endpoint, VERIFY_CERTS, logger=logger)
    properties_stack = run_property_toolkit(resource, utilities)
    try:
        tracking_id = properties_stack["OneFuse_Tracking_Id"]
    except KeyError:
        tracking_id = ""
    hostname = properties_stack["onefuse_name"]
    ip_address = properties_stack["onefuse_ip"]
    response_json = ofm.provision_dns(dns_policy, properties_stack,
                                      hostname, ip_address, zones,
                                      tracking_id)
    utilities.check_or_create_cf("OneFuse_Dns_Nic0")
    response_json["endpoint"] = onefuse_endpoint
    resource.OneFuse_Dns_Nic0 = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
    dns_strings = create_dns_strings(response_json["records"])
    resource.set_value_for_custom_field("onefuse_dns", dns_strings)
    resource.save()


def create_dns_strings(records):
    dns_strings = []
    for record in records:
        dns_string = (f'Type: {record[type]}, Name: {record["name"]}, '
                      f'Value: {record["value"]}')
        dns_strings.append(dns_string)
    return dns_strings


def onefuse_ad(resource, utilities):
    logger.debug(f"Provisioning Active Directory as a Service for resource: "
                 f"{resource}")
    properties_stack = run_property_toolkit(resource, utilities)
    onefuse_endpoint = properties_stack["OneFuse_DnsPolicy_Nic0"].split(":")[0]
    dns_policy = properties_stack["OneFuse_DnsPolicy_Nic0"].split(":")[1]
    set_progress(f"Starting OneFuse Naming Policy: "
                 f"{dns_policy}, Endpoint: {onefuse_endpoint}")
    from xui.onefuse.globals import VERIFY_CERTS
    ofm = CbOneFuseManager(onefuse_endpoint, VERIFY_CERTS, logger=logger)
    properties_stack = run_property_toolkit(resource, utilities)
    try:
        tracking_id = properties_stack["OneFuse_Tracking_Id"]
    except KeyError:
        tracking_id = ""
    name = resource.name
    response_json = ofm.provision_ad(dns_policy, properties_stack, name,
                                     tracking_id)
    # Move OU
    response_json = ofm.move_ou(response_json["id"])
    utilities.check_or_create_cf("OneFuse_Dns_Nic0")
    response_json["endpoint"] = onefuse_endpoint
    resource.OneFuse_Dns_Nic0 = json.dumps(response_json)
    resource.OneFuse_Tracking_Id = response_json.get("trackingId")
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
    onefuse_endpoint = properties_stack["OneFuse_NamingPolicy"].split(":")[0]
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
