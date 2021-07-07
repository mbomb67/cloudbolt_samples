"""
Get Options Action for ARM Builder for Environment ID
"""
from accounts.models import Group
from common.methods import set_progress


def get_options_list(field, **kwargs):
    set_progress(f'kwargs: {kwargs}')
    group_name = kwargs["group"]
    set_progress(f'group: {group_name}')
    group = Group.objects.get(name=group_name)
    envs = group.get_available_environments()
    set_progress(f'Available Envs: {envs}')
    options = [('', '--- Select an Environment ---')]
    for env in envs:
        if env.resource_handler.resource_technology.name == "Azure":
            options.append((env.id, env.name))
    return options
