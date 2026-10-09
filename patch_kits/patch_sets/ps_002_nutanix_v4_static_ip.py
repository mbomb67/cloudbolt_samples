"""
ps_002_nutanix_v4_static_ip.py

Monkey patches for the Nutanix Acropolis v4.0 create-VM path.

Patch 1 (original): honor the `ip` kwarg that CloudBolt already passes in
(from IPAM / static IP on sc_nic_0_ip) by adding networkInfo.ipv4Config to the
NIC. Without this, the IP allocated by Infoblox at Pre-Create Resource is
dropped and Nutanix falls back to the subnet's IP pool. VPC subnets with no
pool then fail the build. The v2 and v3 paths already pass the IP; only v4.0
was missing it.

Patch 2 (new): static IPs on UNMANAGED subnets via a cloud-init NoCloud seed.
Nutanix cannot assign an IP on an unmanaged (non-IPAM) subnet, and Nutanix's
native cloud-init config drive carries no network configuration, so the guest
must be told its address by cloud-init's NoCloud datasource. When the server
parameter `acropolis_nocloud_seed` is true, this patch set:

  1. collects IP / prefix / gateway / DNS / hostname in
     AcropolisResourceHandler.get_create_resource_kwargs (fails fast, before
     the VM exists, if anything required is missing),
  2. creates the VM powered off (as the v4 path already does),
  3. reads the NIC's MAC from Nutanix,
  4. builds a `cidata` ISO9660 image (stdlib only, no new packages) holding
     meta-data / network-config / user-data,
  5. writes it under MEDIA_ROOT/cloudinit/ so Apache serves it at
     /static/uploads/cloudinit/<token>.iso,
  6. POST vmm/v4.0/content/images (UrlSource) so Prism Central pulls the ISO,
  7. POST vmm/v4.0/ahv/config/vms/{vm}/cd-roms to attach it,
  8. deletes the local ISO file, then lets the normal power-on proceed.

In seed mode the NIC gets NO ipv4Config (Prism rejects that on unmanaged
subnets) and NO guestCustomization (cloud-init uses exactly one datasource;
operator user-data from `acropolis_user_data` is merged into the seed's
user-data instead).

Managed vs. unmanaged subnets: before building the seed, the patch reads
GET /api/networking/v4.0/config/subnets/{extId} to learn whether Nutanix
manages IPs on that subnet (subnetType OVERLAY, or an ipConfig is present).
- Unmanaged: Prism rejects NIC ipv4Config, so "ip" is dropped from the create
  kwargs and the guest gets its address ONLY from the seed.
- Managed: the external-IPAM (Infoblox) IP is BOTH sent to Nutanix as NIC
  ipv4Config (Patch 1; Nutanix needs it to create the NIC without dipping into
  its own pool — an empty pool otherwise fails the build with
  VM_NIC_ADDRESS_POOL_EXHAUSTED) AND written into the seed so cloud-init
  configures the same address inside the guest.

Seed user account: when credentials are EXPLICITLY set for the build (image /
environment / server — username plus a password and/or an SSH key, resolved by
get_server_credentials with include_default_values=False so the "root" /
"Administrator" fallbacks never trigger this), the seed's user-data also
creates that account: sudoers entry, SHA-512 crypt hash of the password (the
ISO never carries it in plaintext), and the public key derived from the stored
CloudBolt SSH key. When a password is set, sudo requires it and ssh_pwauth is
enabled; key-only accounts get NOPASSWD sudo. Existing accounts (for instance
the image's default user) are modified, not duplicated, by cloud-init.

Patch 3 (new): report the ASSIGNED IP back to CloudBolt. Nutanix does not
repeat a NIC's assigned (networkInfo.ipv4Config) address in
ipv4Info.learnedIpAddresses — verified on-cluster: a VM running on its
assigned IP showed an empty learned list until a second, extra address was
added in-guest, and only that extra address appeared. Upstream's v4 branch of
_convert_nics reads ONLY the learned list, so on managed subnets the IP
CloudBolt itself assigned is invisible: wait_for_ip_to_be_set retries until
timeout and VM sync records a secondary address or none. This patch wraps
_convert_nics so the assigned address becomes the primary 'ip_address' and
learned extras are kept in 'additional_ip_addresses'. Trade-off: the IP wait
now passes at creation time, so it no longer doubles as a guest-boot check.

All Nutanix calls go through CloudBolt's existing PrismRESTClient (plain REST
via `requests`). The JSON property names and URL paths below were checked
against Nutanix's published v4.0 API definition (the generated model files of
the official v4.0 client, which mirror the REST spec):
  vmm.v4.content.Image        {name, description, type, source, clusterLocationExtIds}
  vmm.v4.content.UrlSource    {url, shouldAllowInsecureUrl, basicAuth}
  vmm.v4.ahv.config.CdRom     {diskAddress{busType IDE|SATA, index}, backingInfo}
  vmm.v4.ahv.config.VmDisk    {dataSource{reference: ImageReference{imageExtId}}}
  POST /api/vmm/v4.0/content/images
  POST /api/vmm/v4.0/ahv/config/vms/{vmExtId}/cd-roms
"""
import hashlib
import io
import ipaddress
import os
import secrets
import struct
import time
import uuid
from base64 import b64encode

import yaml
from django.conf import settings

from common import byte_units
from resourcehandlers.acropolis.acropolis_wrapper import (
    AcropolisTaskFailure,
    AcropolisTechnologyWrapper,
)
from resourcehandlers.acropolis.models import AcropolisResourceHandler
from utilities.exceptions import CloudBoltException
from utilities.models import SSHKey
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# ---------------------------------------------------------------------------
# Site configuration for the NoCloud seed (edit for the target appliance)
# ---------------------------------------------------------------------------
# Server parameter (Boolean) that turns the seed path on for a build. Create a
# Parameter named exactly this and set it True on the environment / blueprint
# used for static-IP builds. On managed subnets the IP is also sent to Nutanix
# as NIC ipv4Config; on unmanaged subnets the seed alone carries it.
SEED_PARAMETER_NAME = "acropolis_nocloud_seed"

# Base URL Prism Central will use to download the ISO from this appliance.
# Leave empty to use the default Portal's configured domain (Admin > Portals).
# Must be reachable from Prism Central on 443, e.g. "https://cloudbolt.example.com".
CLOUDBOLT_PUBLIC_BASE_URL = ""

# Tell Prism Central to ignore certificate errors when pulling the ISO. Leave
# True unless the appliance certificate is trusted by Prism Central.
ALLOW_INSECURE_ISO_URL = True

# Where the ISO is written (must be served by Apache) and the matching URL path.
# MEDIA_ROOT is /var/www/html/cloudbolt/static/uploads/, served at /static/uploads/.
SEED_SUBDIR = "cloudinit"
SEED_URL_PATH = "/static/uploads/" + SEED_SUBDIR + "/"

# CD-ROM bus for the seed on the default "PC" (i440fx) machine type. IDE is the
# AHV default for CD-ROMs. VMs created with UEFI / Secure Boot use machine type
# Q35, which has no IDE controller, so those always get SATA regardless of this.
SEED_CDROM_BUS = "IDE"

# Seconds to wait for Prism Central to finish importing the ISO.
IMAGE_IMPORT_TIMEOUT = 300


# ---------------------------------------------------------------------------
# Patch 2a: gather the seed inputs while we still have the Server object
# ---------------------------------------------------------------------------
def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    return bool(value)


def _prefix_from_netmask(netmask):
    try:
        return ipaddress.IPv4Network(
            "0.0.0.0/{}".format(netmask), strict=False
        ).prefixlen
    except (ValueError, ipaddress.AddressValueError, ipaddress.NetmaskValueError) as exc:
        raise CloudBoltException(
            "acropolis_nocloud_seed: invalid IPv4 subnet mask '{}': {}".format(
                netmask, exc
            )
        )


def _seed_base_url():
    base = (CLOUDBOLT_PUBLIC_BASE_URL or "").strip()
    if not base:
        from portals.models import PortalConfig

        portal = PortalConfig.get_current_portal()
        base = (portal.site_url or "") if portal else ""
    base = base.strip().rstrip("/")
    if not base.lower().startswith(("http://", "https://")):
        raise CloudBoltException(
            "acropolis_nocloud_seed: cannot determine this appliance's public URL. "
            "Set CLOUDBOLT_PUBLIC_BASE_URL in ps_002_nutanix_v4_static_ip.py or set "
            "the default Portal's domain (Admin > Portals)."
        )
    return base


def _seed_user_spec(rh, server):
    """Return {'name', 'passwd_hash', 'ssh_keys'} for the seed's user account.

    Only built from credentials someone explicitly set (image / environment /
    server); include_default_values=False keeps the implicit "root" /
    "Administrator" / "ubuntu" fallbacks from creating accounts. Returns None
    when no usable account is configured.
    """
    creds = rh.get_server_credentials(
        server.id,
        raise_exception_if_keyfile_not_on_cb=False,
        arm_for_use=False,
        include_default_values=False,
    )
    username = (creds.get("username") or "").strip()
    if not username:
        return None

    password = creds.get("password") or None
    passwd_hash = None
    if password:
        # SHA-512 crypt so the ISO (which persists as a Prism Central image)
        # never contains the plaintext. crypt is POSIX-only; the appliance is
        # Linux, so import here rather than at module scope.
        import crypt

        passwd_hash = crypt.crypt(password, crypt.mksalt(crypt.METHOD_SHA512))

    ssh_keys = []
    key_name, key_is_global = server.get_ssh_key_creds_from_cfvs()
    if key_name:
        key = SSHKey.objects.filter(
            resource_handler=None if key_is_global else rh, name=key_name
        ).first()
        public_key = key.cast().public_key if key else None
        if public_key:
            ssh_keys.append(public_key)
        else:
            logger.warning(
                "acropolis_nocloud_seed: SSH key '%s' is set for server %s but no "
                "public key could be derived from it; the seed account will not "
                "get an authorized key.",
                key_name,
                server.id,
            )

    if not passwd_hash and not ssh_keys:
        logger.warning(
            "acropolis_nocloud_seed: username '%s' is set for server %s but there "
            "is no password or resolvable SSH key; not creating the account.",
            username,
            server.id,
        )
        return None

    return {"name": username, "passwd_hash": passwd_hash, "ssh_keys": ssh_keys}


def _build_seed_spec(rh, server, rendered_user_data):
    """Return the dict create_v4_vm needs to build and attach the NoCloud seed.

    Raises CloudBoltException on any missing input so the job fails BEFORE the
    VM is created rather than leaving a half-built VM in Nutanix.
    """
    ip = (getattr(server, "sc_nic_0_ip", None) or "").strip()
    if not ip or ip.lower() == "dhcp":
        raise CloudBoltException(
            "acropolis_nocloud_seed is set but the server has no static IP on "
            "sc_nic_0_ip (got '{}'). Assign an IP (IPAM or manual) or unset the "
            "parameter.".format(ip)
        )
    try:
        ipaddress.IPv4Address(ip)
    except ValueError:
        raise CloudBoltException(
            "acropolis_nocloud_seed: '{}' is not a valid IPv4 address.".format(ip)
        )

    network = getattr(server, "sc_nic_0", None)
    nic = server.nics.first() if hasattr(server, "nics") else None

    netmask = (nic.netmask if nic and nic.netmask else None) or (
        network.netmask if network and network.netmask else None
    )
    if not netmask:
        raise CloudBoltException(
            "acropolis_nocloud_seed: no subnet mask found. Set the netmask on the "
            "server's NIC or on the selected network '{}'.".format(
                getattr(network, "name", "?")
            )
        )
    prefix = _prefix_from_netmask(netmask)

    gateway = (nic.gateway if nic and nic.gateway else None) or (
        network.gateway if network and network.gateway else None
    )
    if gateway:
        try:
            ipaddress.IPv4Address(gateway)
        except ValueError:
            raise CloudBoltException(
                "acropolis_nocloud_seed: invalid gateway '{}'.".format(gateway)
            )

    # DNS: server parameter first (comma separated), then the network's dns1/dns2.
    dns_servers = []
    dns_cf = server.get_value_for_custom_field("domain_name_server") or ""
    if dns_cf:
        dns_servers = [d.strip() for d in str(dns_cf).split(",") if d.strip()]
    elif network:
        dns_servers = [d for d in (network.dns1, network.dns2) if d]
    dns_domain = server.get_value_for_custom_field("dns_domain") or ""
    if not dns_domain and network:
        dns_domain = network.dns_domain or ""

    hostname = (server.hostname or "").strip()
    if not hostname:
        raise CloudBoltException("acropolis_nocloud_seed: server has no hostname.")

    return {
        "server_pk": int(server.pk),
        "hostname": hostname,
        "dns_domain": dns_domain,
        "ip": ip,
        "prefix": prefix,
        "gateway": gateway,
        "dns_servers": dns_servers,
        "user": _seed_user_spec(rh, server),
        "user_data": rendered_user_data,
        "base_url": _seed_base_url(),  # validated now so we fail before create
    }


def _subnet_is_managed(rh, network):
    """Ask Nutanix whether it manages IPs (IPAM) on the server's NIC 0 subnet.

    Managed subnets must also receive the Infoblox IP as NIC ipv4Config
    (Patch 1) or Nutanix allocates from its own pool; unmanaged subnets reject
    ipv4Config, so there the seed alone carries the IP. Overlay (VPC) subnets
    are always managed; a VLAN subnet is managed when it carries an ipConfig.
    Read live because the synced ResourceNetwork does not record this for v4.0.
    """
    subnet_ext_id = getattr(network, "network", None)
    if not subnet_ext_id:
        raise CloudBoltException(
            "acropolis_nocloud_seed: server has no Nutanix subnet on NIC 0."
        )
    wrapper = rh.get_api_wrapper()
    uri = "networking/{}/config/subnets/{}".format(wrapper.api_version, subnet_ext_id)
    subnet = wrapper.client.get(uri).get("data") or {}
    managed = subnet.get("subnetType") == "OVERLAY" or bool(subnet.get("ipConfig"))
    logger.info(
        "acropolis_nocloud_seed: subnet %s (%s) subnetType=%s ipConfig=%s -> %s",
        subnet.get("name"),
        subnet_ext_id,
        subnet.get("subnetType"),
        bool(subnet.get("ipConfig")),
        "managed" if managed else "unmanaged",
    )
    return managed


def patch_nutanix_v4_seed_kwargs():
    original = AcropolisResourceHandler.get_create_resource_kwargs

    def get_create_resource_kwargs(self, server):
        kwargs = original(self, server)
        if not _truthy(getattr(server, SEED_PARAMETER_NAME, False)):
            return kwargs
        if self.nutanix_api_version != "v4.0":
            raise CloudBoltException(
                "acropolis_nocloud_seed is only supported with Nutanix API version "
                "v4.0 (this handler is '{}').".format(self.nutanix_api_version)
            )
        if kwargs.get("is_windows"):
            logger.warning(
                "acropolis_nocloud_seed set on Windows server %s; cloud-init NoCloud "
                "seeding is Linux-only. Ignoring the parameter.",
                server.id,
            )
            return kwargs

        # Operator user-data was already rendered by the original method; move it
        # into the seed so it is NOT also sent as native guestCustomization.
        rendered_user_data = kwargs.pop("acropolis_user_data", None)
        kwargs["nocloud_seed"] = _build_seed_spec(self, server, rendered_user_data)

        # The seed always configures the guest. Whether Nutanix ALSO gets the IP
        # depends on the subnet:
        # - managed: keep "ip" so Patch 1 sends it as NIC ipv4Config (dropping it
        #   makes Nutanix allocate from its own pool, which fails with
        #   VM_NIC_ADDRESS_POOL_EXHAUSTED when the pool is empty),
        # - unmanaged: drop "ip" because Prism rejects ipv4Config there.
        managed = _subnet_is_managed(self, kwargs.get("network"))
        if not managed:
            kwargs.pop("ip", None)
        logger.info(
            "acropolis_nocloud_seed: server %s will get %s/%s gw=%s dns=%s via seed "
            "(subnet is %s; Nutanix ipv4Config %s)",
            server.id,
            kwargs["nocloud_seed"]["ip"],
            kwargs["nocloud_seed"]["prefix"],
            kwargs["nocloud_seed"]["gateway"],
            kwargs["nocloud_seed"]["dns_servers"],
            "managed" if managed else "unmanaged",
            "will also be sent" if managed else "will not be sent",
        )
        return kwargs

    AcropolisResourceHandler.get_create_resource_kwargs = get_create_resource_kwargs


# ---------------------------------------------------------------------------
# Patch 2b: seed content (cloud-init NoCloud files)
# ---------------------------------------------------------------------------
def _normalize_mac(value):
    raw = str(value or "").strip().lower().replace("-", "").replace(":", "")
    if len(raw) != 12:
        return ""
    return ":".join(raw[i : i + 2] for i in range(0, 12, 2))


def _build_meta_data(seed):
    meta = {
        "instance-id": "cb-{}-v1".format(seed["server_pk"]),
        "local-hostname": seed["hostname"],
    }
    return yaml.safe_dump(meta, default_flow_style=False, sort_keys=True).encode(
        "utf-8"
    )


def _build_network_config(seed, mac):
    """cloud-init network-config v2, MAC-matched to the NIC Nutanix assigned.

    Default route uses the explicit 0.0.0.0/0 form: `to: default` fails on the
    NetworkManager renderer used by EL8/EL9 images.
    """
    entry = {
        "match": {"macaddress": mac},
        "dhcp4": False,
        "dhcp6": False,
        "addresses": ["{}/{}".format(seed["ip"], seed["prefix"])],
    }
    if seed.get("gateway"):
        entry["routes"] = [{"to": "0.0.0.0/0", "via": seed["gateway"]}]
    if seed.get("dns_servers"):
        entry["nameservers"] = {"addresses": list(seed["dns_servers"])}
        if seed.get("dns_domain"):
            entry["nameservers"]["search"] = [seed["dns_domain"]]
    doc = {"version": 2, "ethernets": {"eth0": entry}}
    return yaml.safe_dump(doc, default_flow_style=False, sort_keys=False).encode(
        "utf-8"
    )


def _housekeeping_user_data(seed):
    """CloudBolt's own cloud-config: hostname housekeeping plus, when the build
    has explicit credentials, the seed user account (sudoers + password hash
    and/or SSH public key)."""
    doc = {
        "manual_cache_clean": True,
        "preserve_hostname": False,
        "hostname": seed["hostname"],
    }
    if seed.get("dns_domain"):
        doc["fqdn"] = "{}.{}".format(seed["hostname"], seed["dns_domain"])

    user = seed.get("user")
    if user:
        entry = {"name": user["name"], "shell": "/bin/bash"}
        if user["name"] != "root":
            # Passworded accounts must sudo with their password; key-only
            # accounts have no password to give, so they get NOPASSWD.
            entry["sudo"] = (
                "ALL=(ALL) ALL" if user.get("passwd_hash") else "ALL=(ALL) NOPASSWD:ALL"
            )
        if user.get("passwd_hash"):
            entry["passwd"] = user["passwd_hash"]
            entry["lock_passwd"] = False
        if user.get("ssh_keys"):
            entry["ssh_authorized_keys"] = list(user["ssh_keys"])
        # Keep the image's default user ("default") alongside the new account;
        # a bare users: list would otherwise replace it.
        doc["users"] = ["default", entry]
        if user.get("passwd_hash"):
            # CloudBolt itself connects with these credentials over SSH, so
            # password auth must be accepted by sshd.
            doc["ssh_pwauth"] = True

    return "#cloud-config\n# CloudBolt NoCloud seed housekeeping.\n" + yaml.safe_dump(
        doc, default_flow_style=False, sort_keys=False
    )


_USER_DATA_CONTENT_TYPES = (
    ("#cloud-config", "text/cloud-config"),
    ("#!", "text/x-shellscript"),
    ("#include", "text/x-include-url"),
    ("#part-handler", "text/part-handler"),
    ("#cloud-boothook", "text/cloud-boothook"),
)


def _content_type_for(payload):
    for line in payload.splitlines():
        s = line.strip()
        if not s:
            continue
        for prefix, ctype in _USER_DATA_CONTENT_TYPES:
            if s.startswith(prefix):
                return ctype
        return "text/plain"
    return "text/plain"


def _build_user_data(seed):
    """Housekeeping cloud-config, plus the operator's acropolis_user_data (if any)
    bundled as multipart/mixed so cloud-init applies both."""
    housekeeping = _housekeeping_user_data(seed)
    operator = (seed.get("user_data") or "").replace("\r\n", "\n").replace("\r", "\n")
    if not operator.strip():
        return housekeeping.encode("utf-8")

    digest = hashlib.sha256((housekeeping + operator).encode("utf-8")).hexdigest()
    boundary = "===CB-CIDATA-" + digest[:16] + "==="
    if boundary in operator:
        raise CloudBoltException(
            "acropolis_user_data unexpectedly contains the generated MIME boundary."
        )
    parts = [
        'Content-Type: multipart/mixed; boundary="{}"'.format(boundary),
        "MIME-Version: 1.0",
        "",
        "--{}".format(boundary),
        'Content-Type: text/cloud-config; charset="utf-8"',
        "MIME-Version: 1.0",
        "Content-Transfer-Encoding: 8bit",
        "",
        housekeeping.rstrip("\n"),
        "",
        "--{}".format(boundary),
        'Content-Type: {}; charset="utf-8"'.format(_content_type_for(operator)),
        "MIME-Version: 1.0",
        "Content-Transfer-Encoding: 8bit",
        "",
        operator.rstrip("\n"),
        "",
        "--{}--".format(boundary),
        "",
    ]
    return "\n".join(parts).encode("utf-8")


# ---------------------------------------------------------------------------
# Patch 2c: minimal ISO9660 + Rock Ridge writer (stdlib only)
# ---------------------------------------------------------------------------
_SECTOR = 2048


def _both16(v):
    return struct.pack("<H", v) + struct.pack(">H", v)


def _both32(v):
    return struct.pack("<I", v) + struct.pack(">I", v)


def _pad_sector(b):
    r = len(b) % _SECTOR
    return b + b"\x00" * (_SECTOR - r) if r else b


def _dir_record(extent, length, ident, is_dir, now, rock_ridge=b""):
    """ECMA-119 9.1 directory record. `rock_ridge` is the System Use area."""
    tm = time.gmtime(now)
    rec = bytearray()
    rec += b"\x00\x00"  # [0] LEN_DR (set below), [1] extended attribute length
    rec += _both32(extent)  # [2..9]  location of extent
    rec += _both32(length)  # [10..17] data length
    rec += bytes(
        [tm.tm_year - 1900, tm.tm_mon, tm.tm_mday, tm.tm_hour, tm.tm_min, tm.tm_sec, 0]
    )
    rec += bytes([0x02 if is_dir else 0x00])  # [25] flags
    rec += b"\x00\x00"  # [26] unit size, [27] interleave gap
    rec += _both16(1)  # [28..31] volume sequence number
    rec += bytes([len(ident)])  # [32] LEN_FI
    rec += ident
    if len(ident) % 2 == 0:
        rec += b"\x00"  # pad so the System Use area starts on an even offset
    rec += rock_ridge
    if len(rec) % 2:
        rec += b"\x00"  # records must have even length
    rec[0] = len(rec)
    return bytes(rec)


def _rr_sp():
    # SUSP "SP" entry on the root '.' record: tells Linux Rock Ridge is present.
    return b"SP" + bytes([7, 1, 0xBE, 0xEF, 0])


def _rr_nm(name):
    # RRIP "NM" entry: the real (lowercase, hyphenated) file name.
    name = name.encode("ascii")
    return b"NM" + bytes([5 + len(name), 1, 0]) + name


def _path_table(root_extent, big_endian):
    f32, f16 = (">I", ">H") if big_endian else ("<I", "<H")
    return (
        bytes([1, 0])
        + struct.pack(f32, root_extent)
        + struct.pack(f16, 1)
        + b"\x00\x00"
    )


def build_cidata_iso(files, volume_id="cidata", pad_sectors=150):
    """Build an ISO9660 image (with Rock Ridge names) from {name: bytes}.

    Layout: 16 system sectors, PVD @16, terminator @17, L/M path tables @18/19,
    root directory @20, file extents from @21, then `pad_sectors` of padding
    (genisoimage's default). The ISO9660 identifiers are UPPERCASE;1 (Linux's
    default map=normal turns META-DATA;1 into meta-data even without Rock Ridge)
    and the Rock Ridge NM entries carry the exact names.
    """
    now = time.time()
    lpt_s, mpt_s, root_s = 18, 19, 20
    next_s = 21
    layout = []
    for name, data in files.items():
        data = data or b""
        layout.append((name, (name.upper() + ";1").encode("ascii"), next_s, data))
        next_s += max(1, (len(data) + _SECTOR - 1) // _SECTOR)
    total = next_s + pad_sectors

    root = _dir_record(root_s, _SECTOR, b"\x00", True, now, rock_ridge=_rr_sp())
    root += _dir_record(root_s, _SECTOR, b"\x01", True, now)
    for name, ident, extent, data in layout:
        root += _dir_record(
            extent, len(data), ident, False, now, rock_ridge=_rr_nm(name)
        )
    if len(root) > _SECTOR:
        raise CloudBoltException(
            "NoCloud seed: too many files for a one-sector root directory."
        )
    root = _pad_sector(root)

    lpt, mpt = _path_table(root_s, False), _path_table(root_s, True)

    def vol_date(t):
        return time.strftime("%Y%m%d%H%M%S00", time.gmtime(t)).encode("ascii") + b"\x00"

    pvd = bytearray(_SECTOR)
    pvd[0] = 1
    pvd[1:6] = b"CD001"
    pvd[6] = 1
    pvd[8:40] = b"LINUX".ljust(32)
    pvd[40:72] = volume_id.encode("ascii").ljust(32)
    pvd[80:88] = _both32(total)
    pvd[120:124] = _both16(1)
    pvd[124:128] = _both16(1)
    pvd[128:132] = _both16(_SECTOR)
    pvd[132:140] = _both32(len(lpt))
    pvd[140:144] = struct.pack("<I", lpt_s)
    pvd[148:152] = struct.pack(">I", mpt_s)
    pvd[156:190] = _dir_record(root_s, _SECTOR, b"\x00", True, now)
    pvd[190:318] = b" " * 128
    pvd[318:446] = b"CLOUDBOLT".ljust(128)
    pvd[446:574] = b"CLOUDBOLT".ljust(128)
    pvd[574:702] = b"CLOUDBOLT NOCLOUD SEED".ljust(128)
    pvd[702:739] = b" " * 37
    pvd[739:776] = b" " * 37
    pvd[776:813] = b" " * 37
    pvd[813:830] = vol_date(now)
    pvd[830:847] = vol_date(now)
    pvd[847:864] = b"0" * 16 + b"\x00"
    pvd[864:881] = vol_date(now)
    pvd[881] = 1

    term = bytearray(_SECTOR)
    term[0] = 255
    term[1:6] = b"CD001"
    term[6] = 1

    out = io.BytesIO()
    out.write(b"\x00" * (_SECTOR * 16))
    out.write(pvd)
    out.write(term)
    out.write(_pad_sector(lpt))
    out.write(_pad_sector(mpt))
    out.write(root)
    for name, ident, extent, data in layout:
        assert out.tell() == extent * _SECTOR
        out.write(_pad_sector(data) if data else b"\x00" * _SECTOR)
    out.write(b"\x00" * (_SECTOR * pad_sectors))
    iso = out.getvalue()
    assert len(iso) == total * _SECTOR
    return iso


# ---------------------------------------------------------------------------
# Patch 2d: publish the ISO and attach it to the VM via the v4 REST API
# ---------------------------------------------------------------------------
def _publish_seed_iso(iso_bytes, base_url):
    """Write the ISO where Apache serves it; return (local_path, url)."""
    seed_dir = os.path.join(settings.MEDIA_ROOT, SEED_SUBDIR)
    os.makedirs(seed_dir, exist_ok=True)
    filename = "{}.iso".format(secrets.token_urlsafe(24))
    local_path = os.path.join(seed_dir, filename)
    with open(local_path, "wb") as fh:
        fh.write(iso_bytes)
    os.chmod(local_path, 0o644)
    return local_path, base_url + SEED_URL_PATH + filename


def _image_ext_id_from_task(task_result):
    for entity in task_result.get("entitiesAffected") or []:
        if "image" in str(entity.get("rel", "")).lower() and entity.get("extId"):
            return entity["extId"]
    return None


def attach_nocloud_seed_v4(self, vm_uuid, seed, cluster_uuid, machine_type="PC"):
    """Build, publish and attach the NoCloud seed ISO to a powered-off v4 VM."""
    vm_url = "vmm/{}/ahv/config/vms/{}".format(self.api_version, vm_uuid)
    images_url = "vmm/{}/content/images".format(self.api_version)
    # Q35 (UEFI / Secure Boot) has no IDE controller; CD-ROMs there must be SATA.
    cdrom_bus = "SATA" if str(machine_type).upper() == "Q35" else SEED_CDROM_BUS

    # 1. MAC of the NIC Nutanix just created (needed for network-config matching).
    _etag, vm_doc = self.client.get_with_etag_v4(vm_url)
    nics = (vm_doc.get("data") or {}).get("nics") or []
    mac = ""
    if nics:
        mac = _normalize_mac((nics[0].get("backingInfo") or {}).get("macAddress"))
    if not mac:
        raise CloudBoltException(
            "NoCloud seed: Nutanix returned no MAC address for VM {} NIC 0: {}".format(
                vm_uuid, nics
            )
        )

    # 2. Seed files -> ISO.
    files = {
        "meta-data": _build_meta_data(seed),
        "network-config": _build_network_config(seed, mac),
        "user-data": _build_user_data(seed),
    }
    iso_bytes = build_cidata_iso(files)
    logger.info(
        "NoCloud seed for VM %s: mac=%s ip=%s/%s gw=%s dns=%s hostname=%s (%d bytes)",
        vm_uuid,
        mac,
        seed["ip"],
        seed["prefix"],
        seed.get("gateway"),
        seed.get("dns_servers"),
        seed["hostname"],
        len(iso_bytes),
    )

    # 3. Publish for Prism Central to pull, create the image, then remove the file.
    local_path, iso_url = _publish_seed_iso(iso_bytes, seed["base_url"])
    # Unique per attempt so a retried build never collides with, or is confused
    # with, an earlier image of the same server (image names are not unique in PC).
    image_name = "cb-cidata-{}-{}-{}".format(
        seed["hostname"], seed["server_pk"], uuid.uuid4().hex[:8]
    )
    try:
        image_payload = {
            "name": image_name,
            "description": "CloudBolt cloud-init NoCloud seed for {} (server {})".format(
                seed["hostname"], seed["server_pk"]
            ),
            "type": "ISO_IMAGE",
            "source": {
                "$objectType": "vmm.v4.content.UrlSource",
                "url": iso_url,
                "shouldAllowInsecureUrl": bool(ALLOW_INSECURE_ISO_URL),
            },
        }
        if cluster_uuid:
            image_payload["clusterLocationExtIds"] = [cluster_uuid]
        logger.info(
            "NoCloud seed: creating Nutanix ISO image %s from %s", image_name, iso_url
        )
        headers = {"NTNX-Request-Id": str(uuid.uuid4())}
        task_uuid, task_result = self.client.post_and_wait_for_task(
            images_url, image_payload, timeout=IMAGE_IMPORT_TIMEOUT, headers=headers
        )
        logger.debug("NoCloud seed: image task %s result %s", task_uuid, task_result)
    finally:
        try:
            os.remove(local_path)
        except OSError:
            logger.warning("NoCloud seed: could not remove %s", local_path)

    image_ext_id = _image_ext_id_from_task(task_result)
    if not image_ext_id:
        # Fallback: look the image up by its unique name.
        listing = self.client.get(
            images_url, querystring={"$filter": "name eq '{}'".format(image_name)}
        )
        for img in listing.get("data") or []:
            if img.get("name") == image_name and img.get("extId"):
                image_ext_id = img["extId"]
                break
    if not image_ext_id:
        raise CloudBoltException(
            "NoCloud seed: image {} was created but its extId could not be determined "
            "from task {}".format(image_name, task_uuid)
        )
    logger.info("NoCloud seed: image %s created with extId %s", image_name, image_ext_id)

    # 4. Attach as a CD-ROM (VM sub-resource POST requires the VM's current ETag).
    etag, _vm_doc = self.client.get_with_etag_v4(vm_url)
    headers = self.get_etag_headers(etag)
    cdrom_payload = {
        "$objectType": "vmm.v4.ahv.config.CdRom",
        "diskAddress": {
            "$objectType": "vmm.v4.ahv.config.CdRomAddress",
            "busType": cdrom_bus,
            "index": 0,
        },
        "backingInfo": {
            "$objectType": "vmm.v4.ahv.config.VmDisk",
            "dataSource": {
                "$objectType": "vmm.v4.ahv.config.DataSource",
                "reference": {
                    "$objectType": "vmm.v4.ahv.config.ImageReference",
                    "imageExtId": image_ext_id,
                },
            },
        },
    }
    logger.info(
        "NoCloud seed: attaching image %s as %s CD-ROM on VM %s (machine type %s)",
        image_ext_id,
        cdrom_bus,
        vm_uuid,
        machine_type,
    )
    task_uuid, task_result = self.client.post_and_wait_for_task(
        vm_url + "/cd-roms", cdrom_payload, headers=headers
    )
    logger.debug("NoCloud seed: cd-rom task %s result %s", task_uuid, task_result)
    logger.info(
        "NoCloud seed attached. Image %s (%s) is left in Prism Central for the POC; "
        "delete it after validating the build.",
        image_name,
        image_ext_id,
    )


# ---------------------------------------------------------------------------
# Patch 1 (+ seed hook): create_v4_vm
# ---------------------------------------------------------------------------
def patch_nutanix_v4_static_ip():
    AcropolisTechnologyWrapper.attach_nocloud_seed_v4 = attach_nocloud_seed_v4

    def create_v4_vm(
        self, name, mem_size, cpu_cnt, image, prov_timeout=300, *args, **kwargs
    ):
        """
        Method to create vm using v4.0 apis

        PATCHED: adds networkInfo.ipv4Config to the NIC when an IP is supplied,
        and attaches a cloud-init NoCloud seed ISO before power-on when
        kwargs["nocloud_seed"] is present (see patch_nutanix_v4_seed_kwargs).
        """
        # Redact sensitive fields before logging request kwargs.
        redact_keys = {
            "acropolis_sysprep_file",
            "acropolis_user_data",
            "password",
            "nocloud_seed",
        }

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
        nocloud_seed = kwargs.get("nocloud_seed")

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
        # In NoCloud seed mode "ip" is only present when the subnet is managed
        # (unmanaged subnets reject ipv4Config, so there the kwargs hook removed
        # it and the guest gets its address from the seed alone).
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
        # spec to build the VM.  This is how guest customization is performed for Linux servers on Acropolis.
        # PATCH: in NoCloud seed mode acropolis_user_data was moved into the seed, so
        # nothing is sent here (cloud-init uses exactly one datasource).
        else:
            acropolis_user_data = kwargs.get("acropolis_user_data", None)
            if acropolis_user_data and not nocloud_seed:
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

            # ---- PATCH: NoCloud seed, while the VM is still powered off ----------
            if nocloud_seed:
                self.attach_nocloud_seed_v4(
                    vm_uuid,
                    nocloud_seed,
                    kwargs.get("cluster_uuid"),
                    machine_type=vm_payload.get("machineType", "PC"),
                )
            # ---- PATCH END ---------------------------------------------------------

            self.update_power_state(vm_uuid, "ON")
            return vm_uuid
        except AcropolisTaskFailure as e:
            self.handle_acropolis_task_failure(e)

    AcropolisTechnologyWrapper.create_v4_vm = create_v4_vm


# ---------------------------------------------------------------------------
# Patch 3: report the assigned (ipv4Config) IP to CloudBolt, not just learned
# ---------------------------------------------------------------------------
def patch_nutanix_v4_nic_assigned_ip():
    """Prefer the NIC's assigned address in converted CloudBolt nic dicts.

    Nutanix only puts addresses it snoops off the wire into
    ipv4Info.learnedIpAddresses; the assigned address lives solely in
    ipv4Config. Upstream _convert_nics (v4 branch) reads only the learned
    list, so without this patch wait_for_ip_to_be_set cannot see the IP that
    was just assigned and VM sync stores the wrong address (or none).
    """
    original = AcropolisTechnologyWrapper._convert_nics

    def _convert_nics(self, nic_dicts):
        converted = original(self, nic_dicts)
        if not getattr(self, "is_api_v4", False):
            return converted
        # The original appends exactly one converted dict per dict entry of
        # nic_dicts, in order, so this zip stays aligned.
        raw_nics = [n for n in nic_dicts if isinstance(n, dict)]
        for raw, conv in zip(raw_nics, converted):
            # PC returns both 'networkInfo' and the newer 'nicNetworkInfo'
            # alias; accept either in case the legacy key ever goes away.
            net_info = raw.get("networkInfo") or raw.get("nicNetworkInfo") or {}
            assigned = (
                (net_info.get("ipv4Config") or {}).get("ipAddress") or {}
            ).get("value")
            if not assigned or conv.get("ip_address") == assigned:
                continue
            extras = [
                s.strip()
                for s in (conv.get("additional_ip_addresses") or "").split(",")
                if s.strip()
            ]
            learned_primary = conv.get("ip_address")
            if learned_primary and learned_primary not in extras:
                extras.insert(0, learned_primary)
            extras = [e for e in extras if e != assigned]
            conv["ip_address"] = assigned
            if extras:
                conv["additional_ip_addresses"] = ", ".join(extras)
            logger.debug(
                "Patch 3: using assigned IP %s as primary for NIC %s "
                "(learned kept as additional: %s)",
                assigned,
                conv.get("mac"),
                extras or None,
            )
        return converted

    AcropolisTechnologyWrapper._convert_nics = _convert_nics
