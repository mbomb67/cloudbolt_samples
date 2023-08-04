"""
Create an NSX-T Network on demand.
Pre-Requisites:
- Create a Resource Handler that is connected to an NSX-T instance
- Create a Network Virtualization Platform to connect to NSX-T. Docs:
    https://docs.cloudbolt.io/articles/#!cloudbolt-latest-docs/managing-network-virtualization
- Ensure that you have at least a single Environment configured with an NSX
  Transport Zone, and a Tier 1 Router
"""
import struct
import socket
import time

import json

from accounts.models import Group
from c2_wrapper import create_custom_field
from common.methods import set_progress
from infrastructure.models import Environment, CustomField
from network_virtualization.models import NetworkVirtualization
from pyVmomi import vim
from network_virtualization.nsx_t.models import NSXTTransportZone, \
    NSXTLogicalRouterGateway
from orders.models import CustomFieldValue
from resourcehandlers.vmware.models import VmwareNetwork
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_env_id(field=None, **kwargs):
    group = kwargs["group"]
    set_progress(f"group: {group}")
    # group = Group.objects.get(name=group_name)
    envs = group.get_available_environments()
    options = [("", "--- Select an Environment ---")]
    for env in envs:
        if env.resource_handler:
            if env.resource_handler.resource_technology:
                if env.resource_handler.resource_technology.name == "VMware vCenter":
                    try:
                        get_nsxt_options_from_env(env)
                        options.append((env.id, env.name))
                    except Exception as e:
                        logger.debug(f'Environment did not have nsxt options '
                                     f'set')
    return options


def generate_options_for_network_groups(field=None, **kwargs):
    group = kwargs["group"]
    options = []
    # group = Group.objects.get(name=group_name)
    options.append((group.id, group.name))
    for subgroup in Group.objects.filter(parent=group):
        options.append((subgroup.id, subgroup.name))
    return options


def run(job, resource=None, **kwargs):
    # Action Inputs
    env_id = int("{{env_id}}")
    network_groups = {{network_groups}}
    network_name = "{{network_name}}"
    ipv4_gateway_cidr = "{{ipv4_gateway_cidr}}"
    env = Environment.objects.get(id=env_id)
    rh = env.resource_handler
    nsxt_transport_zone, nsxt_tier_1 = get_nsxt_options_from_env(env)
    nsx = get_nsx(rh)
    network_segment, tz_name = add_segment(nsx, nsxt_transport_zone, network_name,
                                           ipv4_gateway_cidr)
    tier1_name = attach_segment_to_tier(nsx, nsxt_tier_1, network_segment)
    write_params_to_resource(resource, network_segment, tz_name, env,
                             tier1_name)
    network = add_cloudbolt_network(rh, network_segment, resource)
    add_network_to_groups(network_groups, network, resource)
    return "SUCCESS", "", ""


def get_nsxt_options_from_env(env):
    nsxt_tier_1 = get_cfv_for_field("nsxt_tier_1", env)
    nsxt_transport_zone = get_cfv_for_field("nsxt_transport_zone", env)
    return nsxt_transport_zone, nsxt_tier_1


def get_cfv_for_field(field_name, env):
    query_set = env.custom_field_options.filter(field__name=field_name)
    if query_set.count() > 1:
        raise Exception(f"More than one value was found for field: "
                        f"{field_name}")
    if query_set.count() == 0:
        raise Exception(f"No values were found for field: {field_name}")
    return query_set.first().value


def get_nsx(rh):
    sdn = NetworkVirtualization.objects.filter(
        mappings__resource_handler=rh
    ).first()
    nsx = sdn.get_api_wrapper()
    return nsx


def add_network_to_groups(network_groups, network, resource):
    field = CustomField.objects.get(name="sc_nic_0")
    cfv, _ = CustomFieldValue.objects.get_or_create(field=field, value=network)
    groups_ids = ','.join(str(group) for group in network_groups)
    create_custom_field("nsx_t_group_ids", "Group IDs", "STR",
                        namespace="nsx_t_blueprint")
    resource.set_value_for_custom_field("nsx_t_group_ids", groups_ids)
    for group_id in network_groups:
        group = Group.objects.get(id=group_id)
        group.custom_field_options.add(cfv)
        set_progress(f'Adding Network to CloudBolt group: {group.name}')


def add_segment(nsx, nsxt_transport_zone, network_name, ipv4_gateway_cidr):
    tz = NSXTTransportZone.objects.get(id=nsxt_transport_zone)
    network_segment = nsx.add_segment(
        network_name, network_name, ipv4_gateway_cidr, tz.uuid
    )
    if not network_segment:
        raise Exception("Network segment creation failed, see log for more "
                        "details")
    logger.debug(f'network_segment created: {json.dumps(network_segment)}')
    return network_segment, tz.display_name


def attach_segment_to_tier(nsx, nsxt_tier_1, network_segment):
    tier1_name = NSXTLogicalRouterGateway.objects.get(id=nsxt_tier_1).display_name
    nsx.attach_segment_to_tier(tier1_name, network_segment["id"])
    return tier1_name


def write_params_to_resource(resource, network_segment, tz_name, env, tier1_name):
    create_field_set_value(resource, "nsx_t_connected_gateway",
                           "Connected Gateway", tier1_name)
    create_field_set_value(resource, "nsx_t_transport_zone",
                           "Transport Zone", tz_name)
    create_field_set_value(resource, "nsx_t_segment_name",
                           "Network Segment Name",
                           network_segment["display_name"])
    create_field_set_value(resource, "nsx_t_segment_id",
                           "Network Segment ID", network_segment["id"])
    gateway = network_segment["subnets"][0]["gateway_address"].split('/')[0]
    create_field_set_value(resource, "nsx_t_gateway",
                           "Gateway", gateway)
    create_field_set_value(resource, "nsx_t_network",
                           "Network", network_segment["subnets"][0]["network"])
    create_custom_field("nsx_t_rh_id", "Environment ID", "STR",
                        namespace="nsx_t_blueprint")
    resource.set_value_for_custom_field("nsx_t_rh_id", env.resource_handler.id)
    create_custom_field("nsxt_segment_ref", "Member Segments", "STR",
                        namespace="nsxt_xui")
    resource.set_value_for_custom_field("nsxt_segment_ref",
                                        network_segment["path"])
    resource.name = network_segment["display_name"]
    resource.save()


def create_field_set_value(resource, name, label, value):
    field = create_custom_field(name, label, "STR", show_as_attribute=True,
                                namespace="nsx_t_blueprint")
    resource.set_value_for_custom_field(name, value)


def add_cloudbolt_network(rh, network_segment, resource):
    port_group = wait_for_port_group(rh, network_segment["path"])
    gateway, cidr = network_segment["subnets"][0]["gateway_address"].split('/')
    netmask = get_netmask_from_cidr(cidr)
    logger.info(f'Creating CloudBolt Network for: '
                f'{network_segment["display_name"]}')
    network, _ = VmwareNetwork.objects.get_or_create(
        name=network_segment["display_name"],
        network=port_group._moId,
        dvSwitch=port_group.config.distributedVirtualSwitch.name,
        portgroup_key=port_group.key,
        netmask=netmask,
        gateway=gateway,
        addressing_schema="dhcp",
        adapterType="VMXN3"
    )
    network.resource_handler.add(rh.cast())
    create_custom_field("nsx_t_network_id", "CloudBolt Network ID", "STR",
                        namespace="nsx_t_blueprint")
    add_clusters_to_network(rh, network)

    resource.set_value_for_custom_field("nsx_t_network_id", network.id)
    return network


def add_clusters_to_network(rh, network):
    all_networks = rh.cast().get_all_networks()
    for net in all_networks:
        if net["name"] == network.name:
            clusters = net["clusters"]
    if not clusters:
        logger.warning(f"Clusters for network: {network.name} not found")
        return
    # Yes - newtorks is correct - a misspelling in the src code
    network.add_clusters_to_newtorks(clusters)
    # Removing add network to environments - want this to only be available to
    # the group
    # add_network_to_environments(rh, network, clusters)
    return network


def add_network_to_environments(rh, network, clusters):
    for cluster in clusters:
        envs = rh.environment_set.filter(vmware_cluster=cluster)
        for env in envs:
            env.add_network(network)
        logger.info(f'Added network: {network.name} to environment: {env.name}')


def wait_for_port_group(rh, segment_id):
    max_sleep = 120
    sleep_time = 10
    total_sleep = 0
    port_group = None
    while not port_group:
        port_group = get_port_group_from_segment_id(rh, segment_id)
        logger.info(f'Waiting for Port Group creation. Sleeping {sleep_time} '
                    f'seconds')
        total_sleep = total_sleep + sleep_time
        if total_sleep > max_sleep:
            raise Exception(f"Max sleep exceeded while waiting for creation of"
                            f" port group with segment_id of: {segment_id}")
        time.sleep(sleep_time)
    logger.info(f'port_group: {port_group.name} found')
    return port_group


def get_netmask_from_cidr(cidr):
    host_bits = 32 - int(cidr)
    netmask = socket.inet_ntoa(struct.pack('!I', (1 << 32) - (1 << host_bits)))
    return netmask


def get_port_group_from_segment_id(rh, segment_id):
    vc_rh = rh.cast()
    wrapper = vc_rh.get_api_wrapper()
    si = wrapper._get_connection()
    content = si.RetrieveContent()
    vds_results = _get_vim_objects(content, vim.dvs.VmwareDistributedVirtualSwitch)
    port_group = get_port_group_from_vds(vds_results, segment_id)
    return port_group


def _get_vim_objects(content, vim_type):
    '''Get vim objects of a given type.'''
    return [item for item in content.viewManager.CreateContainerView(
        content.rootFolder, [vim_type], recursive=True
    ).view]


def get_port_group_from_vds(vds_results, segment_id):
    for vds in vds_results:
        for port_group in vds.portgroup:
            try:
                if port_group.config.backingType == 'nsx':
                    segmentId = port_group.config.segmentId
                else:
                    continue
                if not segmentId:
                    continue
            except Exception as e:
                continue
            if segmentId == segment_id:
                return port_group
