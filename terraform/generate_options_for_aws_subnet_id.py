from common.methods import set_progress


def get_options_list(field, **kwargs):
    si = kwargs.get("service_item")
    envs = si.capable_environments()
    options = []
    for env in envs:
        networks = list(env.networks().keys())
        for network in networks:
            option = (network.network, network.name)
            if option not in options:
                options.append(option)
    return {
        'options': options,
    }