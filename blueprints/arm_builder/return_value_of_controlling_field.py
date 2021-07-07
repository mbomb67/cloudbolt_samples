"""
This is a helper action for a blueprint constructed by the ARM Builder
blueprint. This action will return the value of another field. For example,
the ARM Builder prompts a user to select a Resource Group, but the resource
groups that are exposed are filtered based off of the environment that the
user has selected. Some ARM templates may include an input for a resource
group, this action will allow you to pipe the selected resource group to those
other parameters.
"""
from common.methods import set_progress


def get_options_list(field, control_value=None, **kwargs):
    set_progress(f'control_value: {control_value}')
    set_progress(f'kwargs: {kwargs}')
    if not control_value:
        return None
    options = [(control_value, control_value)]
    return options
