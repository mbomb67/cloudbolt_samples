"""
Orchestration action to enable Trusted Launch and convert disk from MBR to GPT
on an Azure VM.

1. Converts the disk from MBR to GPT using the MBR2GPT tool.
2. Enables Trusted Launch and Encrypt at Host on the VM.
3. Enables Boot Diagnostics on the VM.
"""
from common.methods import set_progress
from azure.mgmt.compute.models import (
    SecurityProfile,
    UefiSettings,
    VirtualMachineUpdate,
    BootDiagnostics,
    DiagnosticsProfile
)

from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# Storage URI for Boot Diagnostics - this should be set to your Azure Storage
# account URI. To use the Azure Managed Storage Account, set it to None.
STORAGE_URI = None


def run(job, server=None, **kwargs):
    """
    This function runs the MBR to GPT conversion command on a Windows VM.
    It is intended to be used in an Azure environment where the VM is already set up.
    """
    set_progress("Starting Enable Boot Diagnostics on VM...")

    compute_client = get_compute_client(server)

    # Enable Boot Diagnostics if not already enabled
    enable_boot_diagnostics(compute_client, server)

    set_progress(f"Boot Diagnostics enabled successfully for server "
                 f"{server.hostname}.")

    return "", "", ""



def enable_boot_diagnostics(compute_client, server):
    """
    Enable Boot Diagnostics on the given Azure VM.
    This function modifies the VM's diagnostics profile to enable Boot Diagnostics.
    """
    set_progress(f"Enabling Boot Diagnostics for server {server.hostname}...")

    diagnostics_profile = DiagnosticsProfile(
        boot_diagnostics=BootDiagnostics(
            enabled=True,
            storage_uri=STORAGE_URI
        )
    )

    vm_update = VirtualMachineUpdate(
        diagnostics_profile=diagnostics_profile
    )

    update_vm_and_wait(compute_client, server, vm_update)
    set_progress(f"Boot Diagnostics enabled for server {server.hostname}.")
    return None# Wait for the update to complete


def update_vm_and_wait(compute_client, server, vm_update):
    rg_name = server.azurearmserverinfo.resource_group
    vm_name = server.hostname
    async_update = compute_client.virtual_machines.begin_update(
        resource_group_name=rg_name,
        vm_name=vm_name,
        parameters=vm_update
    )
    async_update.wait()


def deallocate_vm(compute_client, server):
    set_progress(f"Deallocating VM {server.hostname}...")
    poller = compute_client.virtual_machines.begin_deallocate(
        resource_group_name=server.azurearmserverinfo.resource_group,
        vm_name=server.hostname,
    )
    poller.wait()


def get_compute_client(server):
    """
    Get the Azure Compute Management Client.
    """
    rh = server.resource_handler.cast()
    wrapper = rh.get_api_wrapper()
    return wrapper.compute_client