"""
Build item for creating an NSX-T Distributed Firewall Rule 1
"""
from c2_wrapper import create_custom_field
from common.methods import set_progress
from infrastructure.models import Environment
from resources.models import Resource
from servicecatalog.models import ServiceBlueprint
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper, \
    generate_options_for_env_id, create_field_set_value, \
    generate_options_for_nsxt_groups, create_cfv_add_to_list
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_action(field=None, **kwargs):
    return ["ALLOW", "DROP", "REJECT"]


def generate_options_for_source_groups(field=None, **kwargs):
    group = kwargs.get('resource').group
    kwargs['group'] = group
    return generate_options_for_nsxt_groups(field, **kwargs)


def generate_options_for_destination_groups(field=None, **kwargs):
    group = kwargs.get('resource').group
    kwargs['group'] = group
    return generate_options_for_nsxt_groups(field, **kwargs)


def run(job, resource=None, *args, **kwargs):
    # Action Inputs
    env_id = "{{env_id}}"
    rule_name = "{{rule_name}}"
    description = "{{description}}"
    security_policy_id = resource.nsxt_dfw_policy_id
    action = "{{action}}"
    source_group_refs = list({{source_groups}})
    destination_group_refs = list({{destination_groups}})
    group = resource.group
    env = Environment.objects.get(id=env_id)
    rh = env.resource_handler
    nsx = NSXTXUIAPIWrapper(rh)
    rule_id = generate_rule_id(rule_name, group)
    rule = nsx.create_or_update_distributed_firewall_rule(
        rule_id,
        security_policy_id,
        rule_name,
        action,
        source_group_refs,
        destination_group_refs,
        description=description)
    create_rule_resource(rule, resource, rh, description)
    return "", "", ""


def create_rule_resource(rule, parent_resource, rh, description):
    rule_blueprint = ServiceBlueprint.objects.filter(
        name='NSX-T Distributed Firewall Rule').first()
    if rule_blueprint:
        rule_resource = Resource.objects.create(
            blueprint=rule_blueprint,
            resource_type=rule_blueprint.resource_type,
            name=rule["display_name"],
            group=parent_resource.group,
            parent_resource=parent_resource,
            owner=parent_resource.owner,
            description=description)

        set_params_for_resource(rule_resource, rule, rh)
        rule_resource.lifecycle = 'ACTIVE'
        rule_resource.save()
    else:
        logger.warning("NSX-T Distributed Firewall Rule blueprint not found")

    return "SUCCESS", "The Security Rule was successfully added.", ""

def generate_rule_id(rule_name, group):
    rule_id = rule_name.replace(" ", "_").lower()
    rule_id = f'{group.name}_{rule_id}'
    return rule_id


def set_params_for_resource(resource, rule, rh):
    create_field_set_value(resource, "nsxt_dfw_rule_id",
                           "NSX-T DFW Policy ID", rule["id"])
    create_field_set_value(resource, "nsxt_dfw_rule_ref",
                           "NSX-T DFW Policy Path", rule["path"])
    create_custom_field("nsxt_rh_id", "Resource Handler ID", "STR",
                        namespace="nsxt_xui")
    resource.set_value_for_custom_field("nsxt_rh_id", rh.id)
    cfvm = resource.get_cfv_manager()
    create_custom_field("nsxt_source_group_refs", "Source Groups", "STR",
                        namespace="nsxt_xui", allow_multiple=True,
                        show_as_attribute=True)
    for group in rule['source_groups']:
        create_cfv_add_to_list(cfvm, "nsxt_source_group_refs", group)
    create_custom_field("nsxt_destination_group_refs", "Destination Groups",
                        "STR", namespace="nsxt_xui", allow_multiple=True,
                        show_as_attribute=True)
    for group in rule['source_groups']:
        create_cfv_add_to_list(cfvm, "nsxt_destination_group_refs", group)
    resource.name = rule["display_name"]
    resource.save()
