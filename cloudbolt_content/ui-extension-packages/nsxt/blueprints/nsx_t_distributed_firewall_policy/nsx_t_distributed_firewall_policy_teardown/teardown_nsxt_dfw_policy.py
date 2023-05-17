"""
Teardown item for deleting an NSX-T Infrastructure Group
"""
from infrastructure.models import Environment
from resourcehandlers.models import ResourceHandler
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, resource=None, *args, **kwargs):
    policy_id = resource.nsxt_dfw_policy_id
    rh = ResourceHandler.objects.get(id=resource.nsxt_rh_id)
    nsx = NSXTXUIAPIWrapper(rh)
    nsx.delete_distributed_firewall_policy(policy_id)
    return "", "", ""
