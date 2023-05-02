"""
CloudBolt Plug-in that creates snapshot for an OpenStack VM.
"""
from infrastructure.models import ServerSnapshot
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, *args, **kwargs):
    server = job.server_set.first()
    profile = job.owner
    rh = server.resource_handler.cast()
    # Fetch snapshot name from kwargs; but default it to action input.
    name = kwargs.get("snapshot_name", "{{snapshot_name}}")
    description = "{{ description }}"
    job.set_progress("Checking server {} for snapshot...".format(server))
    snapshots_to_delete = list(ServerSnapshot.objects.filter(server=server))
    try:
        new_snapshot = rh.create_snapshot(server, name, description)
        if new_snapshot and snapshots_to_delete:
            logger.info("Found CB-initiated snapshot. Noting for deletion...")
            job_created_msg = server.create_delete_snapshots_job(  # noqa: F841
                profile, snapshots_to_delete
            )
    except Exception as err:
        return ("FAILURE", "", err)
    return ("SUCCESS", "Snapshot created", "")
