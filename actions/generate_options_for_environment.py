from accounts.models import Group
from common.methods import set_progress
from utilities.logger import ThreadLogger
from infrastructure.models import Environment

logger = ThreadLogger(__name__)


def get_options_list(field, control_value=None, control_value_dict=None,**kwargs):
    if not control_value:
        options = [('', '--- First, Select a Datacenter ---')]
        return options
    env = Environment.objects.get(name=control_value)
    options = [(env.id, env.name)]
    return options