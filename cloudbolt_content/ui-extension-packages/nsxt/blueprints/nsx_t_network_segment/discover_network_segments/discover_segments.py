"""
Discover existing NSX-T Network Segments. These can be assigned to the groups
that should own them to further manage.
"""
import socket
import struct
import time

from pyVmomi import vim
from c2_wrapper import create_custom_field
from common.methods import set_progress
from network_virtualization.models import NetworkVirtualization
from resourcehandlers.vmware.models import VsphereResourceHandler, \
    VmwareNetwork
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


RESOURCE_IDENTIFIER = 'nsx_t_segment_id'
# This toggles whether or not CloudBolt networks will be created for each NSX
# Network being imported.
CREATE_NETWORKS_ON_IMPORT = False

def discover_resources(**kwargs):
    discovered_segments = []
    for rh in VsphereResourceHandler.objects.all():
        sdn = NetworkVirtualization.objects.filter(
            mappings__resource_handler=rh
        ).first()
        if not sdn:
            set_progress(f"No Network Virtualization objects were found "
                         f"connected to vSphere RH: {rh.name}. Continuing...")
            continue
        set_progress(f'Connecting to NSX-T endpoint for handler: {rh.name}')
        nsx = sdn.get_api_wrapper()
        try:
            api_url = '/policy/api/v1/infra/segments'
            segments = nsx.get(api_url)
        except Exception as e:
            set_progress(f'NSX-T APi Error for RH: {rh.name}. Moving on to '
                         f'next RH. Error: {e}')
            continue
        for segment in segments["results"]:
            try:
                gateway_address = segment["subnets"][0]["gateway_address"]
                gateway = gateway_address.split('/')[0]
                discovered_segments.append({
                    "name": segment["display_name"],
                    "nsx_t_connected_gateway": get_connected_gateway(segment),
                    "nsx_t_transport_zone": get_transport_zone(nsx, segment),
                    "nsx_t_segment_name": segment["display_name"],
                    "nsx_t_segment_id": segment["id"],
                    "nsx_t_gateway": gateway,
                    "nsx_t_network": segment["subnets"][0]["network"],
                    "nsx_t_rh_id": rh.id,
                    "nsx_t_network_id": get_network_id(rh, segment),
                })
                logger.debug(f'Added network for sync: {segment["display_name"]}')
            except Exception as e:
                logger.debug(f'Import of segment failed: '
                             f'{segment["display_name"]}. Error: {e}')
    return discovered_segments


def get_connected_gateway(segment):
    return segment["connectivity_path"].split('/')[-1]


def get_transport_zone(nsx, segment):
    transport_zone_path = segment["transport_zone_path"]
    api_url = f'/policy/api/v1{transport_zone_path}'
    tz = nsx.get(api_url)
    return tz["display_name"]


def get_network_id(rh, segment):
    if CREATE_NETWORKS_ON_IMPORT:
        network_id = add_cloudbolt_network(rh, segment)
        return network_id
    else:
        return ""


def add_cloudbolt_network(rh, network_segment):
    port_group = wait_for_port_group(rh, network_segment["path"])
    gateway, cidr = network_segment["subnets"][0]["gateway_address"].split('/')
    netmask = get_netmask_from_cidr(cidr)
    logger.info(f'Creating CloudBolt Network for: '
                f'{network_segment["display_name"]}')
    network, _ = VmwareNetwork.objects.get_or_create(
        name=network_segment["display_name"],
        network=port_group.name,
        dvSwitch=port_group.config.distributedVirtualSwitch.name,
        portgroup_key=port_group.key,
        netmask=netmask,
        gateway=gateway,
        addressing_schema="static",
        adapterType="VMXN3"
    )
    network.resource_handler.add(rh.cast())
    return network.id


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
