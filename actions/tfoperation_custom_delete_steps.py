#!/usr/local/bin/python
"""
This plug-in contains the necessary logic to delete a Resource that was created from a
TerraformOperation build item. It should be automatically associated with every such
Resource by the Job that creates it, as its Custom Delete Steps Plug-in. As a result,
it is written with the expectation that it runs as essentially the first step in the
sequence of events in the Delete Resource Job, notably before it tries to delete
any Servers that may have been created.

If this plug-in is deleted, Resources created from Terraform Operations will be able
to be deleted, but the deletion Job will not perform any of this important logic.
"""
from subprocess import CalledProcessError

from generic_jobs.wrapper.wrappers import (
    TerraformExecutableWrapper,
    TERRAFORM_DEFAULT_WORKSPACE,
)
from utilities.logger import ThreadLogger


logger = ThreadLogger(__name__)


def run(job, resource):
    """
    Using the Terraform state stored in the Output object associated with the Resource, perform a
    `terraform destroy`

    :return: The standard Action 3-tuple of (status, output, errors)
    """
    # For now, we assume only 1 Output has been added to the Resource
    output = resource.output_set.first()
    if not output or not output.results.get("output"):
        # if there was no output, its likely because there was an error deploying and there is no actual
        # deployment, so all we really need to do is delete the resource and return
        resource.delete()
        return (
            "WARNING",
            "",
            "This Terraform Resource had no output or output.results (A.K.A it had no state). "
            "This was likely from a failed deployment, "
            "but make sure to corroborate within Terraform that this is the case.",
        )
    if not output.invoker or not output.invoker.executable or not output.build_item:
        return (
            "FAILURE",
            "",
            "This Resource is lacking the reference information required to delete it properly.",
        )

    # Get TF vars (e.g., AWS creds), using mappings from the build item that was deployed
    env_vars = {}
    build_item = output.build_item.cast()
    possible_order_items = [
        j.order_item
        for j in resource.jobs.all()
        if getattr(j.order_item, "service_item_id", None) == build_item.id
    ]
    if possible_order_items:
        tf_order_item = possible_order_items[0]
        # There will always be a parent BlueprintOrderItem, with one BIA per build item
        boi = tf_order_item.parent_order_item.cast()
        bia = boi.blueprintitemarguments_set.filter(service_item=build_item).first()
        env_vars = build_item.populate_env_vars(bia)

    # Get workspace id from the Output results or use "default"
    workspace_id = output.results.get("workspace_id", TERRAFORM_DEFAULT_WORKSPACE)

    # Get the current state from the output, to be used for the temporary state file
    state = output.results.get("output")

    # Make the actual call to TF's destroy
    wrapper = TerraformExecutableWrapper(
        exe_path=output.invoker.executable.file_location.name,
        working_dir=output.build_item.cast().source_directory,
        state=state,
        state_file=f"{job.id}.tfstate",
        workspace_id=workspace_id,
    )
    job.set_progress("Beginning Terraform destroy")
    try:
        workspaces = wrapper.workspace_list(env=env_vars)
        if wrapper.workspace_id not in workspaces:
            wrapper.workspace_new(workspace_id, env=env_vars)
        else:
            wrapper.workspace_select(workspace_id, env=env_vars)
        job.set_progress(f"Using Terraform workspace: {workspace_id}")
    except CalledProcessError:
        job.set_progress(f"Terraform failed to use workspace: {workspace_id}")
        return
    try:
        wrapper.init(env=env_vars)
        job.set_progress(f"Output of Terraform init: {wrapper.stdout}")
    except CalledProcessError:
        job.set_progress("Terraform init failed")
        # There's no sense in trying to continue with the subsequent steps.
        return wrapper.evaluate_output()
    try:
        wrapper.destroy(env=env_vars)
        job.set_progress(f"Output of destroy: {wrapper.stdout}")
    except CalledProcessError:
        job.set_progress("Terraform destroy failed")
    try:
        wrapper.workspace_select("default", env=env_vars)
        wrapper._call(f"workspace delete {workspace_id}", env=env_vars)
        job.set_progress(f"Removed workspace: {workspace_id}")
    except CalledProcessError as e:
        logger.debug(f"Terraform workspace failed removal cmd: {e.cmd}, "
                     f"output: {e.output}, stderr: {e.stderr}")
        job.set_progress(f"Failed to remove workspace: {workspace_id}")

    result = wrapper.evaluate_output()
    return result