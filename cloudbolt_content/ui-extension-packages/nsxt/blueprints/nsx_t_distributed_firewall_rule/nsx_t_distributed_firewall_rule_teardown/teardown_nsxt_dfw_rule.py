"""
Teardown item for deleting an NSX-T Firewall Rule
"""
from infrastructure.models import Environment
from resourcehandlers.models import ResourceHandler
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, resource=None, *args, **kwargs):
    rule_id = resource.nsxt_dfw_rule_id
    if not rule_id:
        logger.warning(f"No rule ID found for resource: {resource.name}. "
                       f"Exiting")
        return "", "", ""
    rh = ResourceHandler.objects.get(id=resource.nsxt_rh_id)
    nsx = NSXTXUIAPIWrapper(rh)
    nsx.delete_distributed_firewall_rule(rule_id)
    return "", "", ""
