from common.methods import set_progress
from pyVmomi import vim
from infrastructure.models import Environment
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def get_options_list(field, control_value=None, control_value_dict=None,
                     **kwargs):
    if not control_value:
        options = [('', '--- First, Select an Environment ---')]
        return options
    env = Environment.objects.get(id=control_value)
    return [(n.id, n.name)for n in env.networks()]