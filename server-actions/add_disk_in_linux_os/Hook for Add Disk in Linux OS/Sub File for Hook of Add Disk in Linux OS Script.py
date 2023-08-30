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
        return [(disk.id, disk.name) for disk in disks]
    else:
        return []


def run(job, server=None, *args, **kwargs):
    """
    Expand and mount a disk in the OS of a VM in vCenter.
    """
    disk_id = "{{disk}}"
    disk = server.disks.get(id=disk_id)
    scsi_id = disk.vmware_disk_controller
    mount_name = disk.name.lower().replace(' ', '_')
    linux_scsi_id = f'scsi@{scsi_id.split(":")[0]}:0.{scsi_id.split(":")[1]}.0'
    script = get_linux_script(linux_scsi_id, mount_name)
    response = server.execute_script(script_contents=script)
    set_progress(f"Response: {response}")
    return "SUCCESS", "Disks Expanded Successfully", ""


def get_linux_script(scsi_id, mount_name):
    return """
# Get the device ID of the disk
disk=$(lshw -class disk -businfo | grep ###SCSI_ID### | awk '{print $2}')

# Create a GPT partition table on the disk
parted -s "$disk" mklabel gpt

# Create a single partition using all available space
parted -s "$disk" mkpart primary ext4 0% 100%

# Format the newly created partition as ext4 (replace Y with appropriate partition number)
partition="$disk"1
mkfs.ext4 "$partition"

# Mount the partition to a directory
mountpoint="/mnt/###MOUNT_NAME###"
mkdir -p "$mountpoint"
mount "$partition" "$mountpoint"

# Add an entry to /etc/fstab for automatic mounting on boot
echo "$partition $mountpoint ext4 defaults 0 2" >> /etc/fstab

# Print success message
echo "Disk partitioned, formatted, and mounted at $mountpoint"

""".replace('###SCSI_ID###', scsi_id).replace('###MOUNT_NAME###', mount_name)



