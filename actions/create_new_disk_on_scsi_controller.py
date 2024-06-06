"""
A CloudBolt Plugin that creates a new disk on a SCSI controller in a vCenter VM.
The plugin takes the inputs of the VM, the SCSI controller, and the size of the
disk to be created. The plugin then creates the disk on the specified SCSI
controller. If the SCSI controller does not exist, the plugin will create a new
SCSI controller and attach the disk to it. If the SCSI controller exists, the
plugin will attach the disk to the existing SCSI controller.
"""

from common.methods import set_progress
from infrastructure.models import Server
from shared_modules.vmware import VMwareConnection
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, server=None, resource=None, **kwargs):
    # The bus_number is the numerical key of the SCSI controller
    # Must be less than 4 - only bus numbers 0, 1, 2, 3 are supported
    server_id = "{{blueprint_context.server.server.id}}"
    server = Server.objects.get(id=server_id)
    bus_number = int("{{ bus_number }}")
    disk_size = int("{{ disk_size }}")
    datastore_cluster_name = "{{ datastore_cluster_name }}"
    logger.info(f"Creating a new disk with size: {disk_size} on the SCSI "
                f"controller: {bus_number} for the VM: {server.hostname}")

    # Get the vCenter resource handler
    rh = server.resource_handler.cast()
    vc = VMwareConnection(rh)

    # Get the SCSI controller object
    scsi_controller_obj = vc.get_or_create_scsi_controller(server, bus_number)
    set_progress(f"SCSI Controller Object: {scsi_controller_obj}")

    # Create a new disk on the SCSI controller
    set_progress("Creating a new disk on the SCSI controller...")
    vc.create_disk(server, scsi_controller_obj, disk_size,
                   datastore_cluster_name)
    set_progress(f"New Disk created with {disk_size} GB on SCSI controller "
                 f"{bus_number} for VM {server.hostname}")
    server.refresh_info()
    return "SUCCESS", "Disk created successfully", ""
