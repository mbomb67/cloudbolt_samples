import json
from common.methods import set_progress
from azure.mgmt.resource import PolicyClient
from resourcehandlers.azure_arm.azure_wrapper import configure_arm_client
from utilities.exceptions import CommandExecutionException
from utilities.models import ConnectionInfo
from utilities.run_command import (run_command, execute_command,
                                   execute_script_locally)
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


class AzurePolicyConnection(object):
    def __init__(self, rh):
        if rh is None:
            raise ValueError("Resource Handler is required")
        rh = rh.cast()
        self.rh = rh
        self.client = self.get_client()

    def get_client(self):
        set_progress("Connecting To Azure...")
        wrapper = self.rh.get_api_wrapper()
        web_client = configure_arm_client(wrapper, PolicyClient)
        set_progress("Connection to Azure established")
        return web_client

    def get_subscription_scope(self):
        """
        Determine the scope string based on the subscription type. This can be
        edited to include other scope types like Resource Groups or Management
        Groups.
        """
        subscription_id = self.rh.cast().serviceaccount
        return f"/subscriptions/{subscription_id}"

    def get_management_group_scope(self, management_group_id):
        """
        Get the scope string for a management group.
        """
        scope = (f"/providers/Microsoft.Management/managementGroups/"
                 f"{management_group_id}")
        return scope

    @staticmethod
    def get_value_for_policy_assignment_param(assignment_params, param_key):
        return assignment_params[param_key].value

    def get_policy_assignment_params(self, policy_assignment_id,
                                     management_group_id=None):
        """
        Retrieve the parameters of a specific policy assignment.
        Returns the parameters of the policy assignment.
        """
        # Extract the policy assignment name from the full ID if necessary
        if "/" in policy_assignment_id:
            policy_assignment_id = policy_assignment_id.split("/")[-1]

        if management_group_id:
            scope = self.get_management_group_scope(management_group_id)
        else:
            scope = self.get_subscription_scope()

        try:
            policy_assignment = self.client.policy_assignments.get(
                scope=scope,
                policy_assignment_name=policy_assignment_id
            )
            return policy_assignment.parameters
        except Exception as e:
            logger.error(f"Error retrieving policy assignment: {e}")
            raise