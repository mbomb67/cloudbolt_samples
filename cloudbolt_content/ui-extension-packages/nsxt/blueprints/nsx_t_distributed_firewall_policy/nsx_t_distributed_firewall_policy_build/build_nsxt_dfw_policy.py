"""
Build item for creating an NSX-T Infrastructure Group
"""
from c2_wrapper import create_custom_field
from common.methods import set_progress
from infrastructure.models import Environment
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper, \
    generate_options_for_env_id, create_field_set_value, \
    generate_options_for_nsxt_groups, create_cfv_add_to_list
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, resource=None, *args, **kwargs):
    # Action Inputs
    env_id = "{{env_id}}"
    policy_name = "{{policy_name}}"
    description = "{{description}}"
    scope = list({{nsxt_groups}})
    group = resource.group
    env = Environment.objects.get(id=env_id)
    rh = env.resource_handler
    nsx = NSXTXUIAPIWrapper(rh)
    policy_id = generate_policy_id(policy_name, group)
    policy = nsx.create_or_update_distributed_firewall_policy(
        policy_id,
        policy_name,
        scope,
        description=description)
    set_params_for_resource(resource, policy, rh)
    return "", "", ""


def generate_policy_id(policy_name, group):
    policy_id = policy_name.replace(" ", "_").lower()
    policy_id = f'{group.name}_{policy_id}'
    return policy_id


def set_params_for_resource(resource, policy, rh):
    create_field_set_value(resource, "nsxt_dfw_policy_id",
                           "NSX-T DFW Policy ID", policy["id"])
    create_field_set_value(resource, "nsxt_dfw_policy_ref",
                           "NSX-T DFW Policy Path", policy["path"])
    create_custom_field("nsxt_rh_id", "Resource Handler ID", "STR",
                        namespace="nsxt_xui")
    resource.set_value_for_custom_field("nsxt_rh_id", rh.id)
    create_custom_field("nsxt_group_refs", "Member Groups", "STR",
                        namespace="nsxt_xui", allow_multiple=True,
                        show_as_attribute=True)
    cfvm = resource.get_cfv_manager()
    for group in policy['scope']:
        create_cfv_add_to_list(cfvm, "nsxt_group_refs", group)
    resource.name = policy["display_name"]
    resource.save()
