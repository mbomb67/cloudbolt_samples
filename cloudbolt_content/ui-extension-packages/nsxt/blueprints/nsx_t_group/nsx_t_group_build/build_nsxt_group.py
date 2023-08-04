"""
Build item for creating an NSX-T Infrastructure Group
"""
import json

from c2_wrapper import create_custom_field
from common.methods import set_progress
from infrastructure.models import Environment
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper, \
    generate_options_for_env_id, create_field_set_value, \
    generate_options_for_nsxt_groups, generate_options_for_nsxt_segments, \
    update_expression_parameters
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, resource=None, *args, **kwargs):
    # Action Inputs
    env_id = "{{env_id}}"
    group_name = "{{group_name}}"
    description = "{{description}}"
    # TODO: membership_criteria is not currently used, but will be in the future
    # Use a Custom Form to capture complex inputs for membership criteria
    # criteria = "membership_criteria"
    # logger.debug(f'criteria: {criteria}')
    # criteria = json.loads(criteria.replace("'", '"'))
    nsxt_group_refs = list({{nsxt_groups}})
    nsxt_segment_refs = list({{nsxt_segments}})
    set_progress(f'environment: {env_id}, group_name: {group_name}, '
                 f'description: {description}, nsxt_group_refs: '
                 f'{nsxt_group_refs}, nsxt_segment_refs: {nsxt_segment_refs}')
    group_id = generate_group_id(group_name)
    env = Environment.objects.get(id=env_id)
    rh = env.resource_handler
    nsx = NSXTXUIAPIWrapper(rh)
    group = nsx.create_or_update_infrastructure_groups(group_id, group_name,
                                                       description=description)
    set_params_for_resource(resource, group, rh)
    if nsxt_group_refs or nsxt_segment_refs:
        update_expression_parameters(resource, nsxt_group_refs,
                                     nsxt_segment_refs)
        paths = nsxt_group_refs + nsxt_segment_refs
        nsx.create_or_update_expression(paths, group_id)
    return "", "", ""


def generate_group_id(group_name):
    return group_name.replace(" ", "_").lower()


def set_params_for_resource(resource, group, rh):
    create_field_set_value(resource, "nsxt_group_id",
                           "NSX-T Group ID", group["id"])
    create_field_set_value(resource, "nsxt_group_ref",
                           "NSX-T Group Path", group["path"])
    create_custom_field("nsxt_rh_id", "Resource Handler ID", "STR",
                        namespace="nsxt_xui")
    resource.set_value_for_custom_field("nsxt_rh_id", rh.id)
    create_custom_field("nsxt_group_refs", "Member Groups", "STR",
                        namespace="nsxt_xui", allow_multiple=True,
                        show_as_attribute=True)
    create_custom_field("nsxt_segment_refs", "Member Segments", "STR",
                        namespace="nsxt_xui", allow_multiple=True,
                        show_as_attribute=True)
    resource.name = group["display_name"]
    resource.save()
