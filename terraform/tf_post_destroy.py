"""
Perform teardown and verification logic after executing "destructive" Terraform
subcommands (e.g. `destroy`).
"""

from accounts.models import Group
from jobs.models import Job
from utilities.exceptions import NotFoundException
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def post_destroy(job: Job, group: Group, **kwargs) -> None:
    """
    `post_destroy` runs after `destroy`, after deleting one or more Resources
    provisioned by a Terraform Plan action.

    Note: Any additional side-effects can occur during this function execution,
        but anything returned by this function will not be used.

    Args:
        job (Job): Async "Job" object that's associated with running this `hook`.
        group (Group): "Group" that the original Resource belonged to.
    """
    # Display job progress
    job.set_progress("Running post-destroy for Terraform Plan")

    resource = job.resource_set.first()

    set_historical_servers(resource)

    return


def set_historical_servers(resource):
    # When TF provisions a resource, it will also deprovision, we want to set
    # CB objects to HISTORICAL if they were deleted by TF
    for server in resource.server_set.all():
        created_by_tf = server.get_cfv_for_custom_field(
            "created_by_terraform").value
        if created_by_tf:
            try:
                server.refresh_info()
            except NotFoundException:
                # If here, the server no longer exists, set to HISTORICAL in CB
                logger.info(f'Server: {server.hostname} not found, assuming '
                            f'deleted by TF, setting to Historical')
                server.status = 'HISTORICAL'
                server.save()
