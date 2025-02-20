"""
A Build action that takes in a group and allows you to update a custom field
value for all servers in that group.
"""
from accounts.models import Group
from infrastructure.models import CustomField
from shared_modules.cloudbolt_shared import update_cfvs_for_group_servers
from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_group_id(field, **kwargs):
    return [(str(g.id), g.name) for g in Group.objects.all()]


def generate_options_for_parameter(field, **kwargs):
    cfs = CustomField.objects.all().order_by('label')
    return [(cf.name, f'{cf.label} ({cf.name})') for cf in cfs]


def run(job, *args, **kwargs):
    group_id = '{{ group_id }}'
    field_name = '{{ parameter }}'
    new_value = '{{ new_value }}'
    logger.info(f'Updating Value for Group: {group_id} for parameter: '
                f'{field_name} to {new_value}')
    group = Group.objects.get(id=group_id)
    update_cfvs_for_group_servers(group, field_name, new_value)
    return "SUCCESS", "", ""