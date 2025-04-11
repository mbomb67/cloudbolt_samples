from externalcontent.models import OSBuild
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def get_options_list(field, **kwargs):
    osbs = OSBuild.objects.filter(
        osbuildattribute__resourcehandler__resource_technology__name="VMware vCenter"
    ).distinct()
    return [(os.id, os.name)for os in osbs]