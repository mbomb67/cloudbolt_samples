"""
Day 2 Action for NSX-T Groups, allows adding/removing members from a group
"""
from c2_wrapper import create_custom_field
from common.methods import set_progress
from resourcehandlers.models import ResourceHandler
from resources.models import Resource
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper, get_cf_values, \
    update_expression_parameters


def generate_options_for_nsxt_groups(field=None, **kwargs):
    resource = kwargs.get('resource')
    nsxt_group_id = resource.nsxt_group_id
    current_groups = get_cf_values(resource, "nsxt_group_refs")
    group = resource.group
    nsxt_groups = Resource.objects.filter(group=group,
                                          resource_type__name='nsxt_group',
                                          lifecycle='ACTIVE')
    options = []
    initial_values = []
    for nsxt_group in nsxt_groups:
        if nsxt_group.nsxt_group_id != nsxt_group_id:
            options.append((nsxt_group.nsxt_group_ref, nsxt_group.name))
            if nsxt_group.nsxt_group_ref in current_groups:
                initial_values.append(nsxt_group.nsxt_group_ref)
    return {"options": options, "initial_value": initial_values}


def generate_options_for_nsxt_segments(field=None, **kwargs):
    resource = kwargs.get('resource')
    current_segments = get_cf_values(resource, "nsxt_segment_refs")
    group = resource.group
    nsxt_segments = Resource.objects.filter(
        group=group,
        resource_type__name='nsxt_network_segment',
        lifecycle='ACTIVE')
    options = []
    initial_values = []
    for nsxt_segment in nsxt_segments:
        options.append((nsxt_segment.nsxt_segment_ref, nsxt_segment.name))
        if nsxt_segment.nsxt_segment_ref in current_segments:
            initial_values.append(nsxt_segment.nsxt_segment_ref)
    return {"options": options, "initial_value": initial_values}


def run(job, resource=None, **kwargs):
    # Action Inputs
    nsxt_group_refs = list({{ nsxt_groups }})
    nsxt_segment_refs = list({{ nsxt_segments }})
    nsxt_group_id = resource.nsxt_group_id
    rh = ResourceHandler.objects.get(id=resource.nsxt_rh_id)
    nsx = NSXTXUIAPIWrapper(rh)
    update_expression_parameters(resource, nsxt_group_refs, nsxt_segment_refs)
    paths = nsxt_group_refs + nsxt_segment_refs
    nsx.create_or_update_expression(paths, nsxt_group_id)
    return "SUCCESS", "", ""
