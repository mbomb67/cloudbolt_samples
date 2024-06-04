"""
A CloudBolt Plugin that creates a new disk on a SCSI controller in a vCenter VM.
The plugin takes the inputs of the VM, the SCSI controller, and the size of the
disk to be created. The plugin then creates the disk on the specified SCSI
controller. If the SCSI controller does not exist, the plugin will create a new
SCSI controller and attach the disk to it. If the SCSI controller exists, the
plugin will attach the disk to the existing SCSI controller.
"""

from common.methods import set_progress
from pyVmomi import vim

from infrastructure.models import Server
from resourcehandlers.vmware.tools import tasks
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
    search_index, service_instance = get_search_index_from_rh(rh)

    # Get the vCenter VM
    vc_vm = get_vc_vm_from_server(server, search_index)

    # Get the SCSI controller object
    scsi_controller_obj = get_or_create_scsi_controller(vc_vm, bus_number,
                                                        service_instance)
    set_progress("SCSI Controller Object: %s" % scsi_controller_obj)

    # Create a new disk on the SCSI controller
    set_progress("Creating a new disk on the SCSI controller...")
    create_disk(vc_vm, scsi_controller_obj, disk_size, service_instance, datastore_cluster_name)
    set_progress(f"New Disk created with {disk_size} GB on SCSI controller "
                 f"{bus_number} for VM {server.hostname}")

    return "SUCCESS", "Disk created successfully", ""


def get_or_create_scsi_controller(vc_vm, bus_number, service_instance):
    """
    Get or create a SCSI controller on the VM object. If the preceding
    controllers don't exist this will create them.
    :param vm_obj: VM object
    :param scsi_controller: SCSI controller name
    :return: SCSI controller object
    """
    highest_bus_number = get_highest_bus_number(vc_vm)
    while highest_bus_number < bus_number:
        highest_bus_number += 1
        set_progress("Creating a new SCSI controller...")
        create_scsi_controller(vc_vm, service_instance, highest_bus_number)
    scsi_controller_obj = get_scsi_controller(vc_vm, bus_number)
    return scsi_controller_obj


def get_vc_vm_from_server(server, search_index):
    # Using the search_index get the Virtual Machine from pyvmomi matching the
    # server instance uuid
    vm = search_index.FindByUuid(None, server.vmwareserverinfo.instance_uuid,
                                 True, True)
    if not vm:
        raise Exception(f"VM not found in vCenter for server {server.hostname}")
    return vm


def get_search_index_from_rh(rh):
    pyvmomi_wrapper = rh.get_api_wrapper()
    # Get the pyvmomi server object
    service_instance = pyvmomi_wrapper._get_connection()
    search_index = service_instance.content.searchIndex
    return search_index, service_instance


def list_scsi_controllers_for_vc_vm(vc_vm):
    """
    List all SCSI controllers for a vCenter VM.
    :param vc_vm: vCenter VM object
    :return: List of SCSI controller objects
    """
    scsi_controllers = []
    for device in vc_vm.config.hardware.device:
        if isinstance(device, vim.vm.device.VirtualSCSIController):
            scsi_controllers.append(device)
    return scsi_controllers


def get_scsi_controller(vc_vm, bus_number):
    """
    Get the SCSI controller object from the vCenter VM.
    :param vc_vm: vCenter VM object
    :param bus_number: Bus number of the SCSI controller
    :return: SCSI controller object
    """
    for device in list_scsi_controllers_for_vc_vm(vc_vm):
        if device.busNumber == bus_number:
            return device
    return None


def create_scsi_controller(vc_vm, service_instance, bus_number):
    """
    Create a new SCSI controller on the vCenter VM.
    :param vc_vm: vCenter VM object
    :param service_instance: Service instance object
    :return: SCSI controller object
    """
    devices = []
    spec = vim.vm.ConfigSpec()

    controller = vim.vm.device.VirtualDeviceSpec()
    controller.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
    controller.device = vim.vm.device.ParaVirtualSCSIController()
    controller.device.busNumber = bus_number
    controller.device.hotAddRemove = True
    controller.device.sharedBus = 'noSharing'
    controller.device.scsiCtlrUnitNumber = 7
    devices.append(controller)

    spec.deviceChange = devices
    task = vc_vm.ReconfigVM_Task(spec=spec)
    tasks.wait_for_tasks(service_instance, [task])
    return None


def get_highest_bus_number(vc_vm):
    """
    Get the highest SCSI bus number from the SCSI controllers on the vCenter VM.
    :param vc_vm: vCenter VM object
    :return: Highest SCSI bus number
    """
    scsi_controllers = list_scsi_controllers_for_vc_vm(vc_vm)
    bus_numbers = [controller.busNumber for controller in scsi_controllers]
    return max(bus_numbers)


def create_disk(vc_vm, scsi_controller_obj, disk_size, service_instance,
                datastore_cluster_name):
    """
    Create a new disk on the SCSI controller.
    :param vc_vm: vCenter VM object
    :param scsi_controller_obj: SCSI controller object
    :param disk_size: Disk size
    :param service_instance: Service instance object
    :param datastore_cluster_name: Datastore cluster name
    :return: Disk object
    """
    datastore_cluster = get_datastore_cluster_by_name(service_instance,
                                                        datastore_cluster_name)
    ds = get_recommended_datastore(service_instance,
                                   datastore_cluster_obj=datastore_cluster)
    disk_unit_number = get_disk_unit_number_for_scsi_controller(
        vc_vm, scsi_controller_obj
    )

    devices = []
    spec = vim.vm.ConfigSpec()

    disk = vim.vm.device.VirtualDeviceSpec()
    disk.fileOperation = "create"
    disk.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
    disk.device = vim.vm.device.VirtualDisk()
    disk.device.capacityInKB = disk_size * 1024
    disk.device.controllerKey = scsi_controller_obj.key
    disk.device.unitNumber = disk_unit_number
    disk.device.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
    disk.device.backing.thinProvisioned = True
    disk.device.backing.diskMode = 'persistent'
    disk.device.backing.datastore = ds
    disk.device.backing.fileName = (f'[{ds.name}]/{vc_vm.name}/{vc_vm.name}_'
                                    f'{disk_unit_number}.vmdk')
    devices.append(disk)

    spec.deviceChange = devices
    task = vc_vm.ReconfigVM_Task(spec=spec)
    tasks.wait_for_tasks(service_instance, [task])
    return


def get_vm_disks(vc_vm):
    """
    Get the disks attached to the vCenter VM.
    :param vc_vm: vCenter VM object
    :return: List of disk objects
    """
    disks = []
    for device in vc_vm.config.hardware.device:
        if isinstance(device, vim.vm.device.VirtualDisk):
            disks.append(device)
    return disks


def get_disks_for_scsi_controller(vc_vm, scsi_controller_obj):
    """
    Get the disks attached to the SCSI controller.
    :param vc_vm:
    :param scsi_controller:
    :return:
    """
    disks = get_vm_disks(vc_vm)
    scsi_controller_disks = []
    for disk in disks:
        if disk.controllerKey == scsi_controller_obj.key:
            scsi_controller_disks.append(disk)
    return scsi_controller_disks


def get_disk_unit_number_for_scsi_controller(vc_vm, scsi_controller_obj):
    """
    Get the unit number of the disk to be created.
    :param vc_vm: vCenter VM object
    :return: Unit number of the disk
    """
    disk_unit_numbers = [
        disk.unitNumber for disk in
        get_disks_for_scsi_controller(vc_vm, scsi_controller_obj)
    ]
    unit_number = max(disk_unit_numbers) + 1 if disk_unit_numbers else 0
    if unit_number == 7:
        unit_number += 1
    if unit_number >= 16:
        print("we don't support this many disks")
    return unit_number


def get_cluster_from_vm(vc_vm):
    """
    Get the cluster object from the vCenter VM.
    :param vc_vm: vCenter VM object
    :return: Cluster object
    """
    return vc_vm.resourcePool.parent


def get_datastore_clusters_available_in_cluster(cluster):
    """
    Get the datastore clusters available in the cluster.
    :param cluster: Cluster object
    :return: List of datastore cluster objects
    """
    datastore_clusters = []
    for child in cluster.datastore:
        parent = child.parent
        if isinstance(parent, vim.StoragePod):
            if parent.summary.name and parent not in datastore_clusters:
                datastore_clusters.append(parent)
    return datastore_clusters


def list_all_datastore_clusters(service_instance):
    """
    List all datastore clusters available in the vCenter.
    :param service_instance: Service instance object
    :return: List of datastore cluster objects
    """
    content = service_instance.content
    obj_view = content.viewManager.CreateContainerView(
        content.rootFolder, [vim.StoragePod], True
    )
    datastore_clusters = obj_view.view
    obj_view.Destroy()
    return datastore_clusters


def get_datastore_cluster_by_name(service_instance, datastore_cluster_name):
    """
    Get the datastore cluster object by name.
    :param service_instance: Service instance object
    :param datastore_cluster_name: Datastore cluster name
    :return: Datastore cluster object
    """
    datastore_clusters = list_all_datastore_clusters(service_instance)
    for datastore_cluster in datastore_clusters:
        if datastore_cluster.summary.name == datastore_cluster_name:
            return datastore_cluster
    raise Exception(f"Datastore cluster not found: {datastore_cluster_name}")


def return_storage_placement_for_datastore_cluster(datastore_cluster):
    """
    Return the storage placement for the datastore cluster.
    :param datastore_cluster: Datastore cluster object
    :return: Storage placement object
    """
    return datastore_cluster.configuration.placement

def get_recommended_datastore(service_instance, datastore_cluster_obj=None):
    """
    Function to return Storage DRS recommended datastore from datastore cluster
    Args:
        datastore_cluster_obj: datastore cluster managed object

    Returns: Name of recommended datastore from the given datastore cluster

    """
    if datastore_cluster_obj is None:
        return None
    # Check if Datastore Cluster provided by user is SDRS ready
    sdrs_status = datastore_cluster_obj.podStorageDrsEntry.storageDrsConfig.podConfig.enabled
    if sdrs_status:
        # We can get storage recommendation only if SDRS is enabled on given datastorage cluster
        pod_sel_spec = vim.storageDrs.PodSelectionSpec()
        pod_sel_spec.storagePod = datastore_cluster_obj
        storage_spec = vim.storageDrs.StoragePlacementSpec()
        storage_spec.podSelectionSpec = pod_sel_spec
        storage_spec.type = 'create'

        try:
            rec = service_instance.content.storageResourceManager.RecommendDatastores(
                storageSpec=storage_spec)
            rec_action = rec.recommendations[0].action[0]
            return rec_action.destination.name
        except Exception:
            # There is some error so we fall back to general workflow
            pass
    datastore = None
    datastore_freespace = 0
    for ds in datastore_cluster_obj.childEntity:
        if isinstance(ds,
                      vim.Datastore) and ds.summary.freeSpace > datastore_freespace:
            # If datastore field is provided, filter destination datastores
            datastore = ds
            datastore_freespace = ds.summary.freeSpace
    if datastore:
        return datastore
    return None
