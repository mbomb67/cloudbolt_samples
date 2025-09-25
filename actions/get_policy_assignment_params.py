from common.methods import set_progress
from shared_modules.azure_policy import AzurePolicyConnection
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


POLICY_ASSIGNMENT_ID = {
    "eso_test_1": "ca9ad9001b0e4947aeca374a",
    "eso_test_2": "bb507501fe6c48069134c69d"
}
PARAM_KEY = "allowedTagValues"
MANAGEMENT_GROUP_ID = "SalesEngineering"


def get_options_list(field, environment=None, group=None, **kwargs):
    rh = environment.resource_handler.cast()
    policy_conn = AzurePolicyConnection(rh)
    assignment_id = POLICY_ASSIGNMENT_ID.get(field.name)
    assignment_params = policy_conn.get_policy_assignment_params(
        assignment_id, MANAGEMENT_GROUP_ID)
    param_values = policy_conn.get_value_for_policy_assignment_param(
        assignment_params, PARAM_KEY)
    return param_values

