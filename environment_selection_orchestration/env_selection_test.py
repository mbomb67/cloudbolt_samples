from common.methods import set_progress
from externalcontent.models import OSBuild
from infrastructure.models import Environment
from utilities.logger import ThreadLogger
from resourcehandlers.vmware.models import VsphereResourceHandler

ENVIRONMENT_ID = 122
OS_BUILD_ID = 5

logger = ThreadLogger(__name__)

def determine_deployment_environment_and_parameters(*args, **kwargs):
    logger.info("kwargs are {}".format(kwargs))

    # Use the first environment linked to the less-used cluster
    env = Environment.objects.get(id=ENVIRONMENT_ID)
    parameters = {}
    os_build = OSBuild.objects.get(id=5)

    return {"environment": env, "parameters": parameters, "os-build": os_build}
