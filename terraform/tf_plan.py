"""
Defines the behavior that CloudBolt uses when calling the underlying `terraform
plan` command.
"""

from typing import Dict, List, Tuple

from cbhooks.models import TerraformPlanHook
from jobs.models import Job
from resources.models import Resource
from servicecatalog.models import RunTerraformPlanHookServiceItem
from utilities.logger import ThreadLogger
import ast
import json

logger = ThreadLogger(__name__)

# Type definitions

# Returns flags (List[str]) and environment variables (Dict[str, str]) specific
# to this this command (e.g. `terraform plan`).
output = Tuple[List[str], Dict[str, str]]


def plan(
        hook: TerraformPlanHook,
        job: Job,
        action_inputs: dict,
        resource: Resource,
        service_item: RunTerraformPlanHookServiceItem,
        tf_env_vars: dict,
        state_file_path: str,
        plan_file: str,
        var_file: str = None,
        **kwargs,
) -> output:
    """
    `plan` runs after `init`, and returns flags and environment variables used
    by the underlying `terraform plan` command.

    Note: This function _must_ return the `output` Tuple. Any additional
        side-effects can occur during this function execution, but changing
        the return type will cause Terraform execution to break.

    Args:
        hook (TerraformPlanHook): The "Terraform Plan" Action that's called from
            a Blueprint.
        job (Job): Async "Job" object that's associated with running this `hook`.
        action_inputs (dict): Map of key:value variables that are passed to this
            Terraform Action.
        resource (Resource): "Resource" object that Terraform will populate /
            provision to.
        service_item (RunTerraformPlanHookServiceItem): The Blueprint item
            associated with this "Terraform Plan" Action.
        tf_env_vars (Dict[str, str]): Environment variables used by Terraform
            for this command (`terraform plan`).
        state_file_path (str): Absolute file path to the generated Terraform
            state file.
        plan_file (str): Absolute file path to the Terraform plan file.
        var_file (str): (Optional) Absolute file path to the Terraform
            variables file.
    """
    job.set_progress("Running Terraform plan.")

    # Set flags to be called by `terraform plan`
    flags: List[str] = ["-no-color", f"-out={plan_file}", "-input=false"]

    # Specify the Terraform state file
    if state_file_path is not None and plan_file is None:
        flags.append(f"-state={state_file_path}")

    # Pass Terraform variables file
    if var_file is not None:
        flags.append(f"-var-file={var_file}")

    # Optionally update the TF environment variables for `terraform plan`
    # tf_env_vars["..."] = ...
    # logger.debug(f'tf_env_vars for plan: {tf_env_vars}')

    try:
        list(tf_env_vars.keys()).index("TF_VAR_vm_names")
        vm_names = tf_env_vars["TF_VAR_vm_names"]
        del tf_env_vars["TF_VAR_vm_names"]
        vm_names = ast.literal_eval(vm_names)
        flag = f"-var 'vm_names={json.dumps(vm_names)}'"
        flags.append(flag)
    except ValueError:
        pass

    return flags, tf_env_vars