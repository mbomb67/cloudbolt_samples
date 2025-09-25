from common.methods import set_progress
from shared_modules.azure_policy import AzurePolicyConnection
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


POLICY_ASSIGNMENT_ID = "9fe7643b6f8d4dbbb71542bc"
PARAM_KEY = "allowedTagValues"


def get_options_list(field, environment=None, group=None, **kwargs):
    rh = environment.resource_handler.cast()
    policy_conn = AzurePolicyConnection(rh)
    assignment_params = policy_conn.get_policy_assignment_params(
        POLICY_ASSIGNMENT_ID)
    param_values = policy_conn.get_value_for_policy_assignment_param(
        assignment_params, PARAM_KEY)
    return param_values

