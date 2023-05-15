"""
Allows users to change the groups associated with a Network
Will only allow the user to select Groups that they are registered to or
child groups
"""

from accounts.models import Group
from common.methods import set_progress
from infrastructure.models import CustomField
from orders.models import CustomFieldValue
from resourcehandlers.vmware.models import VmwareNetwork
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_groups_to_add(field=None, resource=None, **kwargs):
    if resource:
        ogs = resource.nsx_t_group_ids
        if ogs:
            current_groups_ids = [int(g) for g in ogs.split(',')]
        else:
            current_groups_ids = []
        options = []
        group = resource.group
        if group.id not in current_groups_ids:
            options.append((group.id, group.name))
        for subgroup in Group.objects.filter(parent=group):
            if subgroup.id not in current_groups_ids:
                options.append((subgroup.id, subgroup.name))
    else:
        options = []
    return options


def generate_options_for_groups_to_remove(field=None, resource=None, **kwargs):
    if resource:
        ogs = resource.nsx_t_group_ids
        if ogs:
            current_groups = [Group.objects.get(id=g) for g in ogs.split(',')]
        else:
            current_groups = []
        options = [(g.id, g.name) for g in current_groups]
    else:
        options = []
    return options


def run(job, resource=None, **kwargs):
    # Action Inputs
    groups_to_add = list({{groups_to_add}})
    groups_to_remove = list({{groups_to_remove}})
    nsx_t_group_ids = resource.nsx_t_group_ids
    network = VmwareNetwork.objects.get(id=resource.nsx_t_network_id)
    field = CustomField.objects.get(name="sc_nic_0")
    cfv, _ = CustomFieldValue.objects.get_or_create(field=field, value=network)
    if groups_to_add:
        add_network_to_groups(cfv, groups_to_add)
    if groups_to_remove:
        remove_network_from_groups(cfv, groups_to_remove)
    update_groups_cfv(resource, nsx_t_group_ids, groups_to_add,
                      groups_to_remove)
    return "SUCCESS", "", ""


def add_network_to_groups(cfv, groups_to_add):
    for group_id in groups_to_add:
        group = Group.objects.get(id=group_id)
        group.custom_field_options.add(cfv)
        set_progress(f'Added Network to CloudBolt group: {group.name}')
    return None


def remove_network_from_groups(cfv, groups_to_remove):
    for group_id in groups_to_remove:
        group = Group.objects.get(id=group_id)
        group.custom_field_options.remove(cfv)
        set_progress(f'Removed Network from CloudBolt group: {group.name}')
    return None


def update_groups_cfv(resource, nsx_t_group_ids, groups_to_add,
                      groups_to_remove):
    logger.debug(f'nsx_t_group_ids: {nsx_t_group_ids}, groups_to_add: '
                 f'{groups_to_add}, groups_to_remove: {groups_to_remove}')
    if nsx_t_group_ids:
        group_ids = [g for g in nsx_t_group_ids.split(',')]
    else:
        group_ids = []
    for group in groups_to_add:
        if group not in group_ids:
            logger.debug(f'Adding group ID: {group}')
            group_ids.append(group)
    for group in groups_to_remove:
        if group in group_ids:
            logger.debug(f'Removing group ID: {group}')
            group_ids.remove(group)
    logger.debug(f'group_ids: {group_ids}')
    nsx_t_group_ids = ",".join(str(id) for id in group_ids)
    resource.nsx_t_group_ids = nsx_t_group_ids
    resource.save()
