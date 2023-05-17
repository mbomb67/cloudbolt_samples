"""
Teardown item for deleting an NSX-T Infrastructure Group
"""
from infrastructure.models import Environment
from resourcehandlers.models import ResourceHandler
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper, \
    generate_options_for_env_id, create_field_set_value
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, resource=None, *args, **kwargs):
    group_id = resource.nsxt_group_id
    rh = ResourceHandler.objects.get(id=resource.nsxt_rh_id)
    nsx = NSXTXUIAPIWrapper(rh)
    group = nsx.delete_infrastructure_group(group_id)
    return "", "", ""
