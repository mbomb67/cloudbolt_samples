"""
This is a helper action for a blueprint constructed by the ARM Builder
blueprint. This action will return the location that the environment selected
is tied to. For this action to work properly, you will need to
set up your field as dependent on the environment parameter.
"""
from common.methods import set_progress
from infrastructure.models import Environment


def get_options_list(field, control_value=None, **kwargs):
    set_progress(f'control_value: {control_value}')
    set_progress(f'kwargs: {kwargs}')
    if not control_value:
        options = [('', '--- First, Select an Environment ---')]
        return options

    env = Environment.objects.get(id=control_value)
    options = [(env.node_location, env.node_location)]
    return options
