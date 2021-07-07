"""
Get Options Action for ARM Builder for Resource Group
"""
from django.db.models.fields.files import FieldFile

from common.methods import set_progress
from infrastructure.models import Environment


def get_options_list(field, control_value=None, **kwargs):
    set_progress(f'control_value: {control_value}')
    set_progress(f'kwargs: {kwargs}')
    if not control_value:
        options = [('', '--- First, Select an Environment ---')]
        return options

    options = [('', '--- Select a Resource Group ---')]
    env = Environment.objects.get(id=control_value)

    groups = env.custom_field_options.filter(
        field__name='resource_group_arm')
    if groups:
        for g in groups:
            options.append((g.str_value, g.str_value))
        return options
    return [('', 'No Resource Groups found in this Environment')]
