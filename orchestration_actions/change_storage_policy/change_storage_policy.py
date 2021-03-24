"""
Orchestration action to assign a VM to Storage Policy in vCenter

Pre-requisites: 
- Create a Parameter in CloudBolt called 'storage_policy_name'
- Pass in the name of the storage policy needed to assign

"""
if __name__ == '__main__':
    import os
    import sys
    import django
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    sys.path.append('/opt/cloudbolt')
    django.setup()
import time, ssl, re
import pyVmomi
from jobs.models import Job
from common.methods import set_progress
from infrastructure.models import Server
from pyVmomi import vim, pbm, VmomiSupport, SoapStubAdapter
from pyVim.connect import SmartConnect
from resourcehandlers.vmware.pyvmomi_wrapper import get_vm_by_uuid, wait_for_tasks
from resourcehandlers.vmware.models import VsphereResourceHandler
from resourcehandlers.vmware.vmware_41 import TechnologyWrapper

def get_vmware_service_instance(rh):
    rh_api = rh.get_api_wrapper()
    return rh_api._get_connection()

def PbmConnect(stubAdapter, disable_ssl_verification=False):
    sslContext = None
    VmomiSupport.GetRequestContext()["vcSessionCookie"] = \
        stubAdapter.cookie.split('"')[1]
    hostname = stubAdapter.host.split(":")[0]
    pbmStub = SoapStubAdapter(
        host=hostname,
        version="pbm.version.version1",
        path="/pbm/sdk",
        poolSize=0,
        sslContext=sslContext)
    pbmSi = pbm.ServiceInstance("ServicteInstance", pbmStub)
    pbmContent = pbmSi.RetrieveContent()
    return pbmContent

def get_storage_profile_by_name(profileManager, storage_policy_name):
    # Get all the storage profiles
    profileIds = profileManager.PbmQueryProfile(
        resourceType=pbm.profile.ResourceType(resourceType="STORAGE"),
        profileCategory="REQUIREMENT"
    )
    # Pare down the list
    if len(profileIds) > 0:
        storageProfiles = profileManager.PbmRetrieveContent(
            profileIds=profileIds)
    # Get the chosen profile
    for storageProfile in storageProfiles:
        if storageProfile.name == storage_policy_name:
            return storageProfile

def run(job, logger=None, server=None, **kwargs):
    si = None
    for server in job.server_set.all():
        storage_policy_name = server.storage_policy_name
        if storage_policy_name == None: 
            set_progress(f'storage_policy_name not selected, storage policy will not be changed')
            return "", "", ""
        if not si:
            si = get_vmware_service_instance(server.resource_handler.cast())
        vm = get_vm_by_uuid(si, server.resource_handler_svr_id)
        # Connect to Storage Policy API and get policy by name
        VmomiSupport.GetRequestContext()["vcSessionCookie"] = \
        si._stub.cookie.split('"')[1]
        hostname = si._stub.host.split(":")[0]
        pbmStub = SoapStubAdapter(
            host=hostname,
            version="pbm.version.version1",
            path="/pbm/sdk",
            poolSize=0 ,
            sslContext=ssl._create_unverified_context())
        pbmServiceInstance = pbm.ServiceInstance("ServiceInstance", pbmStub)
        profileManager = pbmServiceInstance.RetrieveContent().profileManager
        profile = get_storage_profile_by_name(profileManager, storage_policy_name)
        # Set vmware storage policy profile on VM Home and disk
        spec = vim.vm.ConfigSpec()
        deviceSpecs = []
        profileSpecs = []
        profileSpec = vim.vm.DefinedProfileSpec()
        profileSpec.profileId = profile.profileId.uniqueId
        profileSpecs.append(profileSpec)
        spec.vmProfile = profileSpecs
        deviceSpec = vim.vm.device.VirtualDeviceSpec()
        deviceSpec.operation = vim.vm.device.VirtualDeviceSpec.Operation.edit
        for device in vm.config.hardware.device:
            deviceType = type(device).__name__
            if deviceType == "vim.vm.device.VirtualDisk" and re.search('Hard disk (.+)', device.deviceInfo.label).group(1):
                hardwareDevice = device
        deviceSpec.device = hardwareDevice
        deviceSpec.profile = profileSpecs
        deviceSpecs.append(deviceSpec)
        spec.deviceChange = deviceSpecs
        vm.ReconfigVM_Task(spec)
        server.refresh_info()
        return "", "", ""
    return "", "", ""

if __name__ == '__main__':
    job_id = sys.argv[1]
    job = Job.objects.get(id=job_id)
    run = run(job)
    if run[0] == 'FAILURE':
        set_progress(run[1])