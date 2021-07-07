"""
This is a helper action for a blueprint constructed by the ARM Builder
blueprint. This action will return the values available for a parameter on
a selected environment. For this action to work properly, you will need to
set up your field as dependent on the parameter you want to pull dropdowns
from. You will also want to change the FIELD_NAME variable to reflect the
parameter you want to pull from the environment. The values specified in the
environment MUST be part of the allowedValues set on the ARM template parameter
if allowedValues is set on the parameter
"""
from common.methods import set_progress
from infrastructure.models import Environment
FIELD_NAME = 'node_size'

def get_options_list(field, control_value=None, **kwargs):
    set_progress(f'control_value: {control_value}')
    set_progress(f'kwargs: {kwargs}')
    if not control_value:
        options = [('', '--- First, Select an Environment ---')]
        return options

    options = [('', '--- Select a Value ---')]
    env = Environment.objects.get(id=control_value)

    groups = env.custom_field_options.filter(
        field__name=FIELD_NAME)
    if groups:
        for g in groups:
            options.append((g.str_value, g.str_value))
        return options
    return [('', 'No Resource Groups found in this Environment')]
