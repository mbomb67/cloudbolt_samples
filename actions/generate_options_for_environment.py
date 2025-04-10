from accounts.models import Group
from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def get_options_list(field, **kwargs):
    group_name = kwargs["group"]
    set_progress(f"group: {group_name}")
    group = Group.objects.get(name=group_name)
    envs = group.get_available_environments()
    options = [("", "--- Select an Environment ---")]
    for env in envs:
        if env.resource_handler:
            if env.resource_handler.resource_technology:
                if env.resource_handler.resource_technology.name == "VMware vCenter":
                    options.append((env.id, env.name))
    return options