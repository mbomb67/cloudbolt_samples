"""
ps_002_nutanix_v4_static_ip.py

Monkey patch: make the Nutanix Acropolis v4.0 create-VM path honor the `ip`
kwarg that CloudBolt already passes in (from IPAM / static IP on sc_nic_0_ip).

Without this, the IP allocated by Infoblox at Pre-Create Resource is dropped and
Nutanix falls back to the subnet's IP pool. VPC subnets with no pool then fail
the build. The v2 and v3 paths already pass the IP; only v4.0 was missing it.
"""
import uuid
from base64 import b64encode

from common import byte_units
from resourcehandlers.acropolis.acropolis_wrapper import (
    AcropolisTaskFailure,
    AcropolisTechnologyWrapper,
)
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def patch_nutanix_v4_static_ip():

    def create_v4_vm(
        self, name, mem_size, cpu_cnt, image, prov_timeout=300, *args, **kwargs
    ):
        """
        Method to create vm using v4.0 apis

        PATCHED: adds networkInfo.ipv4Config to the NIC when an IP is supplied.
        """
        # Redact sensitive fields before logging request kwargs.
        redact_keys = {"acropolis_sysprep_file", "acropolis_user_data", "password"}

        sanitized_kwargs = kwargs.copy()
        for key in redact_keys:
            if key in sanitized_kwargs:
                sanitized_kwargs[key] = "REDACTED"

        logger.debug(
            f"Create VM: name: {name}, mem_size: {mem_size}, cpu_cnt: {cpu_cnt}, "
            f"image: {image}, kwargs: {sanitized_kwargs}"
        )
        image_disk_size = kwargs["template_disk_size"]
        image_uuid = kwargs["template_uuid"]

        vm_payload = {
            "name": name,
            "source": {"entityType": "VM"},
            "numSockets": 1,
            "numCoresPerSocket": cpu_cnt,
            "memorySizeBytes": int(
                byte_units.convert(mem_size, byte_units.GiB, byte_units.BYTE)
            ),
            "machineType": "PC",
            "disks": [
                {
                    "$objectType": "vmm.v4.ahv.config.Disk",
                    "diskAddress": {
                        "$objectType": "vmm.v4.ahv.config.DiskAddress",
                        "busType": "SCSI",
                        "index": 0,
                    },
                    "backingInfo": {
                        "$objectType": "vmm.v4.ahv.config.VmDisk",
                        "diskSizeBytes": int(
                            byte_units.convert(
                                image_disk_size, byte_units.GiB, byte_units.BYTE
                            )
                        ),
                        "dataSource": {
                            "reference": {
                                "$objectType": "vmm.v4.ahv.config.ImageReference",
                                "imageExtId": image_uuid,
                            },
                            "$objectType": "vmm.v4.ahv.config.DataSource",
                        },
                    },
                }
            ],
            "nics": [
                {
                    "backingInfo": {"model": "VIRTIO"},
                    "networkInfo": {
                        "nicType": "NORMAL_NIC",
                        "vlanMode": "ACCESS",
                        "subnet": {"extId": kwargs.get("network").network},
                    },
                }
            ],
            "cluster": {"extId": kwargs.get("cluster_uuid")},
        }

        # ---- PATCH START -------------------------------------------------------
        # CloudBolt sets kwargs["ip"] from server.sc_nic_0_ip, which the built-in
        # IPAM step (Infoblox allocate_ip) populates before create_resource().
        # The Infoblox plug-in can return the literal string "dhcp"; skip that.
        requested_ip = kwargs.get("ip")
        if requested_ip and requested_ip != "dhcp":
            vm_payload["nics"][0]["networkInfo"]["ipv4Config"] = {
                "$objectType": "vmm.v4.ahv.config.Ipv4Config",
                "shouldAssignIp": True,
                "ipAddress": {
                    "$objectType": "common.v1.config.IPv4Address",
                    "value": requested_ip,
                    "prefixLength": 32,
                },
            }
            logger.info(
                f"Requesting static IP {requested_ip} on NIC 0 for VM {name}"
            )
        # ---- PATCH END ---------------------------------------------------------

        boot_type = kwargs.get("boot_type", "LEGACY")
        if boot_type == "LEGACY":
            vm_payload["bootConfig"] = {
                "$objectType": "vmm.v4.ahv.config.LegacyBoot",
                "bootOrder": ["CDROM", "DISK", "NETWORK"],
            }
        elif boot_type == "SECURE_BOOT":
            vm_payload["machineType"] = "Q35"
            vm_payload["bootConfig"] = {
                "$objectType": "vmm.v4.ahv.config.UefiBoot",
                "isSecureBootEnabled": True,
            }
        elif boot_type == "UEFI":
            vm_payload["machineType"] = "Q35"
            vm_payload["bootConfig"] = {
                "$objectType": "vmm.v4.ahv.config.UefiBoot",
                "isSecureBootEnabled": False,
            }

        is_windows = kwargs.get("is_windows", None)
        if is_windows:
            acropolis_sysprep_file = kwargs.get("acropolis_sysprep_file", None)
            if acropolis_sysprep_file:
                config = {
                    "$objectType": "vmm.v4.ahv.config.Sysprep",
                    "installType": "PREPARED",
                    "sysprepScript": {
                        "$objectType": "vmm.v4.ahv.config.Unattendxml",
                        "value": b64encode(
                            acropolis_sysprep_file.encode("utf-8")
                        ).decode("utf-8"),
                    },
                }
                # Sysprep file could contain a password so we are not logging the value
                logger.info("Provisioning with sysprep")
                vm_payload["guestCustomization"] = {"config": config}

        # If the server is Linux and cloud-init userdata has been provided, we will append the userdata to the API
        # spec to build the VM.  This is how guest customization is performed for Linux servers on Acropolis
        else:
            acropolis_user_data = kwargs.get("acropolis_user_data", None)
            if acropolis_user_data:
                config = {
                    "$objectType": "vmm.v4.ahv.config.CloudInit",
                    "datasourceType": "CONFIG_DRIVE_V2",
                    "cloudInitScript": {
                        "$objectType": "vmm.v4.ahv.config.Userdata",
                        "value": b64encode(acropolis_user_data.encode("utf-8")).decode(
                            "utf-8"
                        ),
                    },
                }
                # userdata could contain a password so we are not logging the value
                logger.info("Provisioning with user_data")
                vm_payload["guestCustomization"] = {"config": config}

        try:
            storage_container_uuid = kwargs.get("storage_container_uuid", None)
            logger.info("VM provisioned to default storage container.")
            vm_url = f"vmm/{self.api_version}/ahv/config/vms"
            headers = {"NTNX-Request-Id": str(uuid.uuid4())}
            # PATCH: log the payload before the POST so the ipv4Config block is
            # visible in the job log. (Upstream logs it after the return, which
            # is unreachable.) guestCustomization is deliberately excluded because
            # it can contain credentials.
            logger.debug(
                "Create VM payload: "
                f"{ {k: v for k, v in vm_payload.items() if k != 'guestCustomization'} }"
            )
            task_uuid, task_result = self.client.post_and_wait_for_task(
                vm_url, vm_payload, headers=headers
            )
            logger.debug(f"Task response:{task_uuid}:{task_result}")
            vm_uuid = self.extract_entity_uuid(task_result)
            if storage_container_uuid and self._should_migrate_v4_disks(
                vm_uuid, storage_container_uuid
            ):
                logger.info(
                    f"Migrating VM {vm_uuid} to storage container {storage_container_uuid}."
                )
                self._migrate_vm_disks_v4(vm_uuid, storage_container_uuid)

            self.update_power_state(vm_uuid, "ON")
            return vm_uuid
        except AcropolisTaskFailure as e:
            self.handle_acropolis_task_failure(e)

    AcropolisTechnologyWrapper.create_v4_vm = create_v4_vm