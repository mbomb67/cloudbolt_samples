"""
CloudBolt Plug-in that adds a new disk in the OS of a VM in vCenter.
"""
from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_disk(server=None, **kwargs):
    """
    Get a list of disks available on the VM.
    """
    if server:
        disks = server.disks.all()
        return [(disk.uuid, disk.name) for disk in disks]
    else:
        return []


def run(job, server=None, *args, **kwargs):
    """
    Expand and mount a disk in the OS of a VM in vCenter.
    """
    disk_uuid = "{{disk}}".replace('-', '').lower()
    script = get_windows_script(disk_uuid)
    response = server.execute_script(script_contents=script)
    set_progress(f"Response: {response}")
    return "SUCCESS", "Disks Expanded Successfully", ""


def get_windows_script(disk_uuid):
    return """
#Address newly added disks
$disk = Get-Disk -SerialNumber ###REPLACE###
Initialize-Disk -Number $disk.Number -PartitionStyle GPT
$DriveLetter = (ls function:[d-z]: -n | ?{ !(test-path $_) })[0] -replace ".$"
$partition = New-Partition -DiskNumber $disk.Number -UseMaximumSize -DriveLetter $DriveLetter
Format-Volume -DriveLetter $DriveLetter -FileSystem NTFS
""".replace('###REPLACE###', disk_uuid)
