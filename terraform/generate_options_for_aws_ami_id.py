from common.methods import set_progress
from resourcehandlers.models import ResourceHandler


def get_options_list(field, **kwargs):
    si = kwargs.get("service_item")
    envs = si.capable_environments()
    options = []
    for env in envs:
        os_builds = env.os_builds.all()
        for osb in os_builds:
            rh = env.resource_handler
            osba = osb.osba_for_resource_handler(rh, env)
            option = (osba.amazonmachineimage.ami_id,
                      osba.os_build.name)
            if option not in options:
                options.append(option)
    return {
        'options': options,
    }