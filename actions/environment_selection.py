from common.methods import set_progress
from externalcontent.models import OSBuild
from infrastructure.models import Environment
from utilities.logger import ThreadLogger
from resourcehandlers.models import ResourceNetwork

logger = ThreadLogger(__name__)


def determine_deployment_environment_and_parameters(*args, **kwargs):
    set_progress("env_select kwargs are {}".format(kwargs))
    net_id = kwargs.get("ssc_network")
    env_id = kwargs.get("ssc_cluster")
    os_id = kwargs.get("ssc_os_build")
    if not env_id or not net_id:
        raise Exception("env_id and net_id are required")
    # Use the first environment linked to the less-used cluster
    env = Environment.objects.get(id=env_id)
    network = ResourceNetwork.objects.get(id=net_id)
    parameters = {
        "sc_nic_0": network,
    }
    os_build = OSBuild.objects.get(id=os_id)
    return_params = {
        "environment": env,
        "parameters": parameters,
        "os-build": os_build

    }
    set_progress(f"env_select return_params: {return_params}")
    return return_params