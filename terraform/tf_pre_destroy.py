"""
Perform global setup logic before executing "destructive" Terraform subcommands
(e.g. `destroy`).
"""

from typing import Dict, Tuple, List

import ast

import json

from common.methods import get_proxies, get_bypass_proxy_domains
from jobs.models import Job
from resources.models import Resource
from utilities.logger import ThreadLogger
from cbhooks.models import TerraformStateFile

logger = ThreadLogger(__name__)

output = Tuple[List[str], Dict[str, str]]


def pre_destroy(
    job: Job,
    resource: Resource,
    state_file_obj: TerraformStateFile,
    state_file_path: str,
    **kwargs,
) -> output:
    """
    `pre_destroy` runs before any "destructive" Terraform subcommands, e.g.
    `destroy`, and sets up the global, required state.

    Note: This function _must_ return a `Dict[str, str]`. Any additional
        side-effects can occur during this function execution, but changing
        the return type will cause Terraform execution to break.

    Args:
        job (Job): Async "Job" object that's associated with running this `hook`.
        resource (Resource): "Resource" object that will be removed by this action.
    """

    # Display job progress
    job.set_progress("Running pre-destroy for Terraform")

    # Add proxy information to TF environment variables.
    tf_env_vars: Dict[str, str] = {}
    proxies = get_proxies("hashicorp.com")
    no_proxies_list = get_bypass_proxy_domains()
    no_proxies = ",".join(no_proxies_list)

    tf_env_vars.setdefault("HTTP_PROXY", proxies.get("http", ""))
    tf_env_vars.setdefault("HTTPS_PROXY", proxies.get("https", ""))
    tf_env_vars.setdefault("NO_PROXY", no_proxies)

    flags = [f"-state={state_file_path}"]

    try:
        list(tf_env_vars.keys()).index("TF_VAR_vm_names")
        vm_names = tf_env_vars["TF_VAR_vm_names"]
        del tf_env_vars["TF_VAR_vm_names"]
        vm_names = ast.literal_eval(vm_names)
        flag = f"-var 'vm_names={json.dumps(vm_names)}'"
        flags.append(flag)
    except ValueError:
        try:
            vm_names = resource.get_cf_values_as_dict()["onefuse_names"]
            flag = f"-var 'vm_names={json.dumps(vm_names)}'"
            flags.append(flag)
        except Exception:
            pass

    return flags, tf_env_vars