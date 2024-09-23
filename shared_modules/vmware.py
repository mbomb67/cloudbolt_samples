"""
This Shared module hosts common methods used for communicating with VMware
"""
from common.methods import set_progress
from pyVmomi import vim

from infrastructure.models import Server
from resourcehandlers.vmware.models import VsphereResourceHandler
from resourcehandlers.vmware.tools import tasks
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


class VMwareConnection(object):
    def __init__(self, rh, server=None):
        """
        Initialize the VMware connection object.
        :param rh: Required. Resource Handler object
        """
        rh = rh.cast()
        if type(rh) is not VsphereResourceHandler:
            raise Exception("Resource Handler is not a VMware Resource Handler")
        self.rh = rh
        (self.search_index, self.service_instance,
         self.content) = self.get_vc_info()

    def get_or_create_scsi_controller(self, server, bus_number):
        """
        Get or create a SCSI controller on the VM object.
        :param server: Server object
        :param bus_number: SCSI controller name
        :return: SCSI controller object
        """
        vc_vm = self.get_vc_vm_from_server(server)
        scsi_controller_obj = self.get_scsi_controller(vc_vm, bus_number)
        if not scsi_controller_obj:
            self.create_scsi_controller(vc_vm, bus_number)
            scsi_controller_obj = self.get_scsi_controller(vc_vm, bus_number)
        return scsi_controller_obj

    def get_vc_vm_from_server(self, server):
        """
        Get the vCenter VM object from the server object.
        :param server:
        :param search_index:
        :return:
        """
        vm = self.search_index.FindByUuid(
            None, server.vmwareserverinfo.instance_uuid, True, True
        )
        if not vm:
            raise Exception(
                f"VM not found in vCenter for server {server.hostname}")
        return vm

    def get_vc_info(self):
        pyvmomi_wrapper = self.rh.get_api_wrapper()
        # Get the pyvmomi server object
        service_instance = pyvmomi_wrapper._get_connection()
        content = service_instance.RetrieveContent()
        search_index = content.searchIndex
        return search_index, service_instance, content

    @staticmethod
    def list_scsi_controllers_for_vc_vm(vc_vm):
        """
        List all SCSI controllers for a vCenter VM. set_vc_vm must be run first.
        :param vc_vm: vCenter VM object
        :return: List of SCSI controller objects
        """
        scsi_controllers = []
        for device in vc_vm.config.hardware.device:
            if isinstance(device, vim.vm.device.VirtualSCSIController):
                scsi_controllers.append(device)
        return scsi_controllers

    def get_scsi_controller(self, vc_vm, bus_number):
        """
        Get the SCSI controller object from the vCenter VM. set_vc_vm must be
        run first.
        :param vc_vm: vCenter VM object
        :param bus_number: Bus number of the SCSI controller
        :return: SCSI controller object
        """
        for device in self.list_scsi_controllers_for_vc_vm(vc_vm):
            if device.busNumber == bus_number:
                return device
        return None

    def create_scsi_controller(self, vc_vm, bus_number):
        """
        Create a new SCSI controller on the vCenter VM. set_vc_vm must be
        run first.
        :param vc_vm: vCenter VM object
        :param bus_number: Bus number of the SCSI controller
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
        tasks.wait_for_tasks(self.service_instance, [task])
        return None

    def get_highest_bus_number(self, vc_vm):
        """
        Get the highest SCSI bus number from the SCSI controllers on the vCenter VM.
        :param vc_vm: vCenter VM object
        :return: Highest SCSI bus number
        """
        scsi_controllers = self.list_scsi_controllers_for_vc_vm(vc_vm)
        bus_numbers = [controller.busNumber for controller in scsi_controllers]
        return max(bus_numbers)

    def create_disk(self, server, scsi_controller_obj, disk_size,
                    datastore_cluster_name):
        """
        Create a new disk on the SCSI controller.
        :param server: Server object
        :param scsi_controller_obj: SCSI controller object
        :param disk_size: Disk size
        :param datastore_cluster_name: Datastore cluster name
        :return: Disk object
        """
        vc_vm = self.get_vc_vm_from_server(server)
        datastore_cluster = self.get_datastore_cluster_by_name(
            datastore_cluster_name
        )
        ds = self.get_recommended_datastore(datastore_cluster)
        disk_unit_number = self.get_disk_unit_number_for_scsi_controller(
            vc_vm, scsi_controller_obj
        )
        disk_number = len(self.get_vm_disks(vc_vm)) + 1

        devices = []
        spec = vim.vm.ConfigSpec()

        disk = vim.vm.device.VirtualDeviceSpec()
        disk.fileOperation = "create"
        disk.operation = vim.vm.device.VirtualDeviceSpec.Operation.add
        disk.device = vim.vm.device.VirtualDisk()
        disk.device.capacityInKB = disk_size * 1024 * 1024
        disk.device.controllerKey = scsi_controller_obj.key
        disk.device.unitNumber = disk_unit_number
        disk.device.backing = vim.vm.device.VirtualDisk.FlatVer2BackingInfo()
        disk.device.backing.thinProvisioned = True
        disk.device.backing.diskMode = 'persistent'
        disk.device.backing.datastore = ds
        disk.device.backing.fileName = (
            f'[{ds.name}]/{vc_vm.name}/{vc_vm.name}_'
            f'{disk_number}.vmdk')
        devices.append(disk)

        spec.deviceChange = devices
        task = vc_vm.ReconfigVM_Task(spec=spec)
        tasks.wait_for_tasks(self.service_instance, [task])
        return

    @staticmethod
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

    def get_disks_for_scsi_controller(self, vc_vm, scsi_controller_obj):
        """
        Get the disks attached to the SCSI controller.
        :param vc_vm: vCenter VM object
        :param scsi_controller_obj: SCSI controller object
        :return:
        """
        disks = self.get_vm_disks(vc_vm)
        scsi_controller_disks = []
        for disk in disks:
            if disk.controllerKey == scsi_controller_obj.key:
                scsi_controller_disks.append(disk)
        return scsi_controller_disks

    def get_disk_unit_number_for_scsi_controller(self, vc_vm,
                                                 scsi_controller_obj):
        """
        Get the unit number of the disk to be created.
        :param scsi_controller_obj: SCSI controller object
        :param vc_vm: vCenter VM object
        :return: Unit number of the disk
        """
        disk_unit_numbers = [
            disk.unitNumber for disk in
            self.get_disks_for_scsi_controller(vc_vm, scsi_controller_obj)
        ]
        unit_number = max(disk_unit_numbers) + 1 if disk_unit_numbers else 0
        if unit_number == 7:
            unit_number += 1
        if unit_number >= 16:
            print("we don't support this many disks")
        return unit_number

    @staticmethod
    def get_cluster_from_vm(vc_vm):
        """
        Get the cluster object from the vCenter VM.
        :param vc_vm: vCenter VM object
        :return: Cluster object
        """
        return vc_vm.resourcePool.parent

    @staticmethod
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

    def list_all_datastore_clusters(self):
        """
        List all datastore clusters available in the vCenter.
        :return: List of datastore cluster objects
        """
        obj_view = self.content.viewManager.CreateContainerView(
            self.content.rootFolder, [vim.StoragePod], True
        )
        datastore_clusters = obj_view.view
        obj_view.Destroy()
        return datastore_clusters

    def get_datastore_cluster_by_name(self, datastore_cluster_name):
        """
        Get the datastore cluster object by name.
        :param datastore_cluster_name: Datastore cluster name
        :return: Datastore cluster object
        """
        datastore_clusters = self.list_all_datastore_clusters()
        for datastore_cluster in datastore_clusters:
            if datastore_cluster.summary.name == datastore_cluster_name:
                return datastore_cluster
        raise Exception(
            f"Datastore cluster not found: {datastore_cluster_name}")

    @staticmethod
    def return_storage_placement_for_datastore_cluster(datastore_cluster):
        """
        Return the storage placement for the datastore cluster.
        :param datastore_cluster: Datastore cluster object
        :return: Storage placement object
        """
        return datastore_cluster.configuration.placement

    def get_recommended_datastore(self, ds_cluster):
        """
        Function to return Storage DRS recommended datastore from datastore
        cluster. If a datastore cluster is not SDRS enabled, it will return
        datastore with most free space.
        Args:
            datastore_cluster_obj: datastore cluster managed object

        :param ds_cluster:
        :return: Name of recommended datastore from the given datastore cluster
        """
        # Check if Datastore Cluster provided by user is SDRS ready
        sdrs_config = ds_cluster.podStorageDrsEntry.storageDrsConfig
        sdrs_status = sdrs_config.podConfig.enabled
        if sdrs_status:
            # We can get storage recommendation only if SDRS is enabled on given
            # datastorage cluster
            pod_sel_spec = vim.storageDrs.PodSelectionSpec()
            pod_sel_spec.storagePod = ds_cluster
            storage_spec = vim.storageDrs.StoragePlacementSpec()
            storage_spec.podSelectionSpec = pod_sel_spec
            storage_spec.type = 'create'

            try:
                rec = self.content.storageResourceManager.RecommendDatastores(
                    storageSpec=storage_spec
                )
                rec_action = rec.recommendations[0].action[0]
                return rec_action.destination.name
            except Exception:
                # There is some error, so we fall back to general workflow
                pass
        datastore = None
        datastore_freespace = 0
        for ds in ds_cluster.childEntity:
            if (isinstance(ds, vim.Datastore) and
                    ds.summary.freeSpace > datastore_freespace):
                # If datastore field is provided, filter destination datastores
                datastore = ds
                datastore_freespace = ds.summary.freeSpace
        if datastore:
            return datastore
        return None

    def get_vm_advanced_info_by_key(self, vc_vm, key):
        """
        Get the advanced info of the vCenter VM by key.
        :param vc_vm: vCenter VM object
        :param key: Key of the advanced info. eg. guestinfo.appInfo
        :return: Advanced info object
        """
        for item in self.get_vm_advanced_info(vc_vm):
            if item.key == key:
                return item.value

    def get_vm_advanced_info(self, vc_vm):
        """
        Get the advanced info of the vCenter VM.
        :param vc_vm: vCenter VM object
        :return: Advanced info object
        """
        return vc_vm.config.extraConfig

    def set_vm_advanced_info(self, vc_vm, key, value):
        """
        Set the advanced info of the vCenter VM.
        :param vc_vm: vCenter VM object
        :param key: Key of the advanced info. eg. guestinfo.myNewKey
        :param value: Value of the advanced info
        :return: None
        """
        extra_config = vc_vm.config.extraConfig
        extra_config.append(vim.option.OptionValue(key=key, value=value))
        spec = vim.vm.ConfigSpec(extraConfig=extra_config)
        task = vc_vm.ReconfigVM_Task(spec=spec)
        tasks.wait_for_tasks(self.service_instance, [task])
