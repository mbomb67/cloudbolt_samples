"""
Create an NSX-T Network on demand
"""
from common.methods import set_progress
from network_virtualization.models import NetworkVirtualization
from resourcehandlers.models import ResourceHandler
from resourcehandlers.vmware.models import VmwareNetwork
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job=None, resource=None, **kwargs):
    # Action Inputs
    rh_id = resource.nsx_t_rh_id
    segment_id = resource.nsx_t_segment_id
    if not segment_id or not rh_id:
        logger.warning(f'A required parameter was not found. segment_id: '
                       f'{segment_id}, rh_id: {rh_id}. Assuming failed '
                       f'deployment - will continue to delete resource')
        return "SUCCESS", "", ""
    rh = ResourceHandler.objects.get(id=rh_id)
    delete_cloudbolt_components(resource)
    delete_nsx_components(rh, segment_id)
    return "SUCCESS", "", ""


def delete_cloudbolt_components(resource):
    network_id = resource.nsx_t_network_id
    if not network_id:
        logger.warning(f'CloudBolt Network with ID of {network_id} could not '
                       f'be found. Assuming that it is already deleted, '
                       f'proceeding to delete any NSX networks.')
        return None
    try:
        network = VmwareNetwork.objects.get(id=network_id)
    except Exception as e:
        logger.warning(f'CloudBolt Network with ID of {network_id} could not '
                       f'be found. Assuming that it is already deleted, '
                       f'proceeding to delete any NSX networks.')
        return None
    nics = network.servernetworkcard_set.all(server__status="ACTIVE")
    # Check to be sure no servers exist on the network before deleting.
    if len(nics) > 0:
        servers = ', '.join(nic.server.hostname for nic in nics)
        raise Exception(f'Unable to delete NSX segment due to active servers. '
                        f'First delete the servers, then try again. Server '
                        f'list: {servers}')
    set_progress(f'Deleting CloudBolt network: {network.name}')
    network.delete()
    return None


def delete_nsx_components(rh, segment_id):
    nsx = get_nsx(rh)
    set_progress(f'Detaching segment {segment_id} from gateway')
    nsx.detach_segment_from_tier(segment_id)
    set_progress(f'Removing segment {segment_id} from NSX-T')
    nsx.remove_segment(segment_id)


def get_nsx(rh):
    sdn = NetworkVirtualization.objects.filter(
        mappings__resource_handler=rh
    ).first()
    nsx = sdn.get_api_wrapper()
    return nsx
