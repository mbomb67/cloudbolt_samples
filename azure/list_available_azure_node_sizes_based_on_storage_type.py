"""
CloudBolt "Generated Parameter Options" plugin for `node_size` that returns the
Azure VM sizes which are actually deployable for the current order, by filtering
the environment's configured sizes through **live Azure Resource SKU
capabilities**.

This expands CloudBolt's native
`cbhooks/hookmodules/limit_azure_node_size_by_storage_type` hook. That hook
starts from the sizes configured on the node_size parameter for the selected
environment and narrows them by storage type using a static name regex. This
plugin keeps the same starting point (the environment's configured sizes) and
the same return shape, but replaces the name heuristics with the authoritative
Resource SKUs API so the surviving sizes are the ones Azure will actually let
you deploy for the selected region, OS image, storage type, and VM security/
networking options.

Attach to the `node_size` custom field (Parameter > Options > Generated) and
wire the relevant order parameters as REGENOPTIONS controllers of `node_size`
(setup notes at the bottom). It is safe to wire controllers that are only
present on some forms: CloudBolt keeps the (global) dependency, still fires this
plugin, and simply omits the absent controller from control_value_dict (API) or
passes it as None (classic form). This plugin reads every controller with
`.get(...)` and filters on it only when a value is actually present.

Base set (matches the native hook)
-----------------------------------
    CustomFieldValue.objects.filter(field=field, environment=environment)
i.e. the node_size values an admin configured for the selected environment. No
allow-list is hard-coded in this plugin -- the environment is the allow-list.

Filters applied (all authoritative, from Azure), each fail-open when unknown
----------------------------------------------------------------------------
For the selected environment's region we pull the VM Resource SKUs once and keep
a size only if ALL applicable constraints hold:
  1. Region / subscription: SKU offered in region and not restricted
     (no `NotAvailableForSubscription`).                        [ResourceSku.restrictions]
  2. Architecture: SKU `CpuArchitectureType` == OS image `.architecture`.   [image]
  3. Hyper-V generation: image `.hyper_v_generation` in SKU `HyperVGenerations`. [image]
  4. Security type (from the `security_type_arm` PARAMETER, not the image;
     unset == Standard == no constraint):
       - "TrustedLaunch"  -> drop SKUs with `TrustedLaunchDisabled` == True.
       - "ConfidentialVM" -> keep only SKUs exposing a confidential-computing
         capability.
  5. Accelerated networking (`enable_accelerated_networking` == True) ->
     require `AcceleratedNetworkingEnabled` == True.
  6. Encryption at host (`encryption_at_host` == True) ->
     require `EncryptionAtHostSupported` == True.
  7. Availability zone (`availability_zone_arm` in {1,2,3}) -> require the zone
     in the SKU's `location_info[].zones` for the region (minus zone-restricted).
  8. Storage: Premium SSD / Ultra -> require `PremiumIO` == True.

Image architecture/generation is read per image type: marketplace (live
VirtualMachineImage lookup), Compute Gallery (image-definition architecture +
generation), managed image (always x64 + real generation), and raw VHD blob
(x64, generation unknown). Arm64 is only possible via a Compute Gallery.

Fail-open: any dimension we cannot determine (SDK/network error, capability
absent, unreadable custom image) is not filtered on, so the order stays
orderable. Set DEBUG_LOGGING = True to emit an "azure_image"-prefixed trace of
every decision point (grep azure_image <logfile>) when troubleshooting.

External API grounding (docs cited at call sites; not extrapolated from memory):
  - Resource SKUs list + capabilities/restrictions:
    https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list
  - Trusted Launch (TrustedLaunchDisabled capability):
    https://learn.microsoft.com/en-us/azure/virtual-machines/trusted-launch-faq
  - Gen1/Gen2 image-vs-size compatibility:
    https://learn.microsoft.com/en-us/azure/virtual-machines/generation-2

Entry point: get_options_list(field, control_value=None, control_value_dict=None,
                              form_data=None, form_prefix=None, **kwargs)
             -> {"options": [(value, label)], "override": True, ...}
"""

from infrastructure.models import Environment
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# Flip to True in an environment where you need to troubleshoot this plugin.
# When True, every decision point emits an "azure_image"-prefixed INFO line
# (grep azure_image <logfile>). Left False so normal order-form renders stay
# quiet. This gates ALL of the plugin's diagnostics, including failure paths.
DEBUG_LOGGING = False


def _log(msg, *args):
    """Troubleshooting log line, prefixed for grep: `grep azure_image <logfile>`.

    No-op unless DEBUG_LOGGING is True; never raises.
    """
    if not DEBUG_LOGGING:
        return
    try:
        logger.info("azure_image " + msg, *args)
    except Exception:  # noqa: BLE001 -- logging must never break option generation
        pass


PARAM_NAME = "node_size"

# Storage types (Azure storageAccountType strings) that require a
# premium-storage-capable size (PremiumIO). "Ultra" also needs zonal
# UltraSSDAvailable, which is covered indirectly by the zone filter but not
# asserted as a standalone capability here.
_PREMIUM_STORAGE_TOKENS = ("premium", "ultra")

# Bare CustomField names of the optional Azure order parameters we filter on.
# All are global (namespace None). security_type_arm is STR with values
# "TrustedLaunch"/"ConfidentialVM" (unset == Standard); availability_zone_arm is
# STR "1"/"2"/"3"; the two networking/encryption params are BOOL (real bools).
CF_SECURITY_TYPE = "security_type_arm"
CF_AVAILABILITY_ZONE = "availability_zone_arm"
CF_ACCELERATED_NETWORKING = "enable_accelerated_networking"
CF_ENCRYPTION_AT_HOST = "encryption_at_host"


# ---------------------------------------------------------------------------
# Controller / context resolution.
#   - single controller  -> control_value
#   - two+ controllers    -> control_value=None, everything in control_value_dict
#     keyed by BARE field name. Absent controller -> key missing (API) or None
#     (classic form). Booleans arrive as real Python bools; the rest as strings.
#     os_build is the only rehydrated one (-> OSBuild instance).
# ---------------------------------------------------------------------------
def _first(value):
    if isinstance(value, (list, tuple)):
        return value[0] if value else None
    return value


def _controllers(control_value_dict, kwargs):
    if isinstance(control_value_dict, dict):
        return control_value_dict
    kw = kwargs.get("control_value_dict")
    return kw if isinstance(kw, dict) else {}


def _get_param(cvd, form_data, form_prefix, kwargs, name):
    """Best-effort fetch of a scalar controller value by bare CF name.

    Order: control_value_dict -> classic form_data (prefixed) -> API order values.
    Returns None when the parameter is absent on this form (the caller then does
    not filter on it). Never raises / never index-accesses.
    """
    val = cvd.get(name)
    if val is not None:
        return _first(val)
    if form_data and form_prefix:
        val = form_data.get(f"{form_prefix}-{name}")
        if val is not None:
            return _first(val)
    api_si_data = kwargs.get("api_si_data")
    if isinstance(api_si_data, dict) and api_si_data.get(name) is not None:
        return _first(api_si_data.get(name))
    return None


def _resolve_environment(kwargs, form_data=None, form_prefix=None):
    candidate = kwargs.get("environment")
    if isinstance(candidate, Environment):
        return candidate
    if candidate not in (None, ""):
        try:
            return Environment.objects.get(id=int(candidate))
        except (Environment.DoesNotExist, ValueError, TypeError):
            pass
    if form_data and form_prefix:
        env_id = _first(form_data.get(f"{form_prefix}-environment"))
        if env_id:
            try:
                return Environment.objects.get(id=int(env_id))
            except (Environment.DoesNotExist, ValueError, TypeError):
                pass
    api_si_data = kwargs.get("api_si_data")
    if isinstance(api_si_data, dict) and api_si_data.get("environment"):
        try:
            return Environment.objects.get(id=int(api_si_data["environment"]))
        except (Environment.DoesNotExist, ValueError, TypeError):
            pass
    return None


def _resolve_os_build(cvd, control_value, form_data=None, form_prefix=None):
    from externalcontent.models import OSBuild

    candidate = cvd.get("os_build")
    if isinstance(candidate, OSBuild):
        return candidate
    if isinstance(control_value, OSBuild):
        return control_value
    candidate = candidate if candidate not in (None, "") else control_value
    if candidate in (None, "") and form_data and form_prefix:
        candidate = form_data.get(f"{form_prefix}-os_build")
    candidate = _first(candidate)
    if candidate in (None, ""):
        return None
    try:
        return OSBuild.objects.get(id=int(candidate))
    except (OSBuild.DoesNotExist, ValueError, TypeError):
        return None


def _resolve_storage_type(cvd, control_value, form_data=None, form_prefix=None):
    def looks_like_storage(v):
        s = str(_first(v) or "").lower()
        return any(tok in s for tok in ("_lrs", "_zrs", "premium", "ultra", "standardssd"))

    for name, value in cvd.items():
        if name == "os_build":
            continue
        if looks_like_storage(value):
            return _first(value)
    if looks_like_storage(control_value):
        return _first(control_value)
    if form_data:
        for value in form_data.values():
            if looks_like_storage(value):
                return _first(value)
    return None


def _as_bool(value):
    """Interpret a BOOL controller that may arrive as a real bool or a string."""
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


# ---------------------------------------------------------------------------
# Base set: the environment's configured node_size values (same as native hook).
# ---------------------------------------------------------------------------
def _env_configured_sizes(field, environment):
    from orders.models import CustomFieldValue

    cfvs = CustomFieldValue.objects.filter(field=field, environment=environment)
    seen = []
    for cfv in cfvs:
        val = getattr(cfv, "value", None)
        if val and val not in seen:
            seen.append(val)
    return seen


# ---------------------------------------------------------------------------
# OS image requirements (architecture / Hyper-V generation). Security type is
# intentionally NOT taken from the image -- it comes from the parameter.
# ---------------------------------------------------------------------------
def _get_azure_image(os_build, rh, env):
    try:
        osba = os_build.osba_for_resource_handler(
            rh, environment=env, region=(getattr(env, "node_location", "") or "")
        )
    except Exception as exc:  # noqa: BLE001
        _log("osba lookup failed for %s: %s", os_build, exc)
        return None
    if osba is None:
        _log("osba_for_resource_handler returned None for os_build=%r rh=%r region=%r",
             getattr(os_build, "name", os_build), getattr(rh, "name", rh),
             getattr(env, "node_location", None))
        return None
    cast = getattr(osba, "cast", None)
    image = cast() if callable(cast) else osba
    _log("resolved OSBA -> %s (class=%s) pub=%r offer=%r sku=%r ver=%r image_id=%r blob_uri=%r",
         getattr(image, "name", image), type(image).__name__,
         getattr(image, "publisher", None), getattr(image, "offer", None),
         getattr(image, "sku", None), getattr(image, "version", None),
         getattr(image, "image_id", None), getattr(image, "blob_uri", None))
    return image


_VM_IMAGE_CACHE = {}


def _image_requirements(rh, image):
    """Return {"architecture", "generation"} for the image (None when unknown).

    Route by the concrete image reference FIRST. CloudBolt populates
    publisher/offer/sku even on gallery/shared and custom images (publisher is a
    placeholder such as 'shared'), so the image_id / blob_uri must take priority
    over the marketplace pub/offer/sku path -- otherwise a gallery image gets
    sent to the marketplace API and fails ("Publisher: shared was not found").

    Cases:
      - Compute Gallery image (.../galleries/.../images/...): the image
        DEFINITION carries architecture (x64/Arm64) + hyperVGeneration.
      - Managed image (.../Microsoft.Compute/images/...): always x64 (Arm64
        requires a Gallery); read the real Hyper-V generation.
      - Raw VHD blob: no ARM resource to query -> x64, generation unknown.
      - Marketplace (real publisher/offer/sku, no custom image_id): live
        VirtualMachineImage lookup.
    Arm64 is ONLY possible via a Compute Gallery.
    """
    reqs = {"architecture": None, "generation": None}
    if image is None:
        return reqs

    image_id = getattr(image, "image_id", None) or ""
    image_id_l = image_id.lower()
    if "/galleries/" in image_id_l:
        return _gallery_image_requirements(rh, image_id)
    if "microsoft.compute/images/" in image_id_l:
        return _managed_image_requirements(rh, image_id)
    if getattr(image, "blob_uri", None):
        _log("VHD blob image -> x64, generation unknown")
        return {"architecture": "x64", "generation": None}

    publisher = getattr(image, "publisher", None)
    offer = getattr(image, "offer", None)
    sku = getattr(image, "sku", None)
    version = getattr(image, "version", None)
    region = getattr(image, "region", None) or getattr(rh, "location", None)
    if not (publisher and offer and sku):
        _log("no image_id/blob and incomplete marketplace ref (pub=%r offer=%r sku=%r) "
             "-> architecture UNKNOWN", publisher, offer, sku)
        return reqs

    _log("marketplace image lookup pub=%r offer=%r sku=%r ver=%r region=%r",
         publisher, offer, sku, version, region)
    key = (publisher, offer, sku, version, region)
    vm_image = _VM_IMAGE_CACHE.get(key, "MISS")
    if vm_image == "MISS":
        vm_image = None
        try:
            wrapper = rh.get_api_wrapper()
            getter = getattr(wrapper, "_get_virtual_machine_image", None) or getattr(
                wrapper, "get_virtual_machine_image", None
            )
            _log("marketplace getter=%s", getattr(getter, "__name__", getter))
            if getter is not None:
                vm_image = getter(publisher, offer, sku, version, region)
        except Exception as exc:  # noqa: BLE001 -- fail open
            _log("marketplace lookup RAISED for %r: %s", key, exc)
            vm_image = None
        _VM_IMAGE_CACHE[key] = vm_image

    if vm_image is not None:
        arch = getattr(vm_image, "architecture", None)
        reqs["architecture"] = str(arch).lower() if arch else None
        gen = getattr(vm_image, "hyper_v_generation", None)
        reqs["generation"] = str(gen).upper() if gen else None
        _log("marketplace vm_image class=%s architecture=%r hyper_v_generation=%r -> reqs=%r",
             type(vm_image).__name__, arch, gen, reqs)
    else:
        _log("marketplace vm_image is None -> architecture UNKNOWN (fail-open, no arch filter)")
    return reqs


def _gallery_image_requirements(rh, image_id):
    """Read architecture/generation from an Azure Compute Gallery image DEFINITION.

    The gallery image definition (Microsoft.Compute/galleries/images) carries the
    `architecture` (x64/Arm64) and `hyper_v_generation` properties -- the image
    version does not override them. Fails open (Nones) on any parse/SDK error.

    Docs: https://learn.microsoft.com/en-us/azure/virtual-machines/image-version
    """
    reqs = {"architecture": None, "generation": None}
    cache_key = ("gallery", image_id)
    gi = _VM_IMAGE_CACHE.get(cache_key, "MISS")
    if gi == "MISS":
        gi = None
        rg = _parse_azure_id_segment(image_id, "resourceGroups")
        gallery = _parse_azure_id_segment(image_id, "galleries")
        # The segment after /images/ is the image DEFINITION name (a trailing
        # /versions/<v> is ignored, which is what we want -- arch lives on the def).
        image_def = _parse_azure_id_segment(image_id, "images")
        _log("gallery lookup rg=%r gallery=%r image_def=%r", rg, gallery, image_def)
        compute = _compute_client(rh) if (rg and gallery and image_def) else None
        if compute is not None:
            try:
                gi = compute.gallery_images.get(rg, gallery, image_def)
            except Exception as exc:  # noqa: BLE001 -- fail open
                _log("gallery lookup RAISED for %s: %s", image_id, exc)
                gi = None
        _VM_IMAGE_CACHE[cache_key] = gi

    if gi is not None:
        arch = getattr(gi, "architecture", None)
        reqs["architecture"] = str(arch).lower() if arch else None
        gen = getattr(gi, "hyper_v_generation", None)
        reqs["generation"] = str(gen).upper() if gen else None
        _log("gallery image architecture=%r hyper_v_generation=%r -> reqs=%r", arch, gen, reqs)
    else:
        _log("gallery image is None -> architecture UNKNOWN (fail-open)")
    return reqs


def _managed_image_requirements(rh, image_id):
    """Read requirements for a managed image (Microsoft.Compute/images).

    Managed images cannot be Arm64 (Arm64 requires a Compute Gallery), so
    architecture is always x64. We still read the real Hyper-V generation
    (Image.hyper_v_generation) so the generation filter applies. Architecture
    stays x64 even if the lookup fails (it cannot be anything else).

    Docs: https://learn.microsoft.com/en-us/azure/virtual-machines/generation-2
    """
    reqs = {"architecture": "x64", "generation": None}
    cache_key = ("managed", image_id)
    img = _VM_IMAGE_CACHE.get(cache_key, "MISS")
    if img == "MISS":
        img = None
        rg = _parse_azure_id_segment(image_id, "resourceGroups")
        name = _parse_azure_id_segment(image_id, "images")
        _log("managed image lookup rg=%r name=%r", rg, name)
        compute = _compute_client(rh) if (rg and name) else None
        if compute is not None:
            try:
                img = compute.images.get(rg, name)
            except Exception as exc:  # noqa: BLE001 -- fail open (architecture stays x64)
                _log("managed image lookup RAISED for %s: %s", image_id, exc)
                img = None
        _VM_IMAGE_CACHE[cache_key] = img

    if img is not None:
        gen = getattr(img, "hyper_v_generation", None)
        reqs["generation"] = str(gen).upper() if gen else None
    _log("managed image -> reqs=%r", reqs)
    return reqs


# ---------------------------------------------------------------------------
# Live Azure Resource SKU capability map for the region.
# ---------------------------------------------------------------------------
_SKU_CACHE = {}
_COMPUTE_CLIENT_CACHE = {}


def _compute_client(rh):
    """Return a cached ComputeManagementClient for the handler, or None."""
    key = getattr(rh, "id", None)
    if key in _COMPUTE_CLIENT_CACHE:
        return _COMPUTE_CLIENT_CACHE[key]
    client = None
    try:
        from azure.mgmt.compute import ComputeManagementClient
        from resourcehandlers.azure_arm.azure_wrapper import configure_arm_client
        client = configure_arm_client(rh.get_api_wrapper(), ComputeManagementClient)
    except Exception as exc:  # noqa: BLE001 -- fail open
        _log("could not build Compute client: %s", exc)
        client = None
    _COMPUTE_CLIENT_CACHE[key] = client
    return client


def _parse_azure_id_segment(resource_id, key):
    """Return the value following /<key>/ in an Azure resource ID (case-insensitive)."""
    parts = [p for p in str(resource_id or "").split("/") if p]
    kl = key.lower()
    for i in range(len(parts) - 1):
        if parts[i].lower() == kl:
            return parts[i + 1]
    return None


def _sku_capability_map(rh, region):
    """Return {size_name: {"caps": {name: value}, "restricted": bool, "zones": set}}.

    Returns None if the SKU list can't be fetched (caller then fails open).
    """
    key = (getattr(rh, "id", None), region)
    if key in _SKU_CACHE:
        return _SKU_CACHE[key]

    compute = _compute_client(rh)
    if compute is None:
        return None

    try:
        # Docs: Resource SKUs - List. OData filter narrows to the region.
        # https://learn.microsoft.com/en-us/rest/api/compute/resource-skus/list
        try:
            skus = compute.resource_skus.list(filter=f"location eq '{region}'")
        except TypeError:
            skus = compute.resource_skus.list()  # older SDK signature
    except Exception as exc:  # noqa: BLE001 -- fail open
        _log("resource_skus.list failed for %s: %s", region, exc)
        return None

    region_l = (region or "").lower()
    result = {}
    try:
        for sku in skus:
            if getattr(sku, "resource_type", None) != "virtualMachines":
                continue
            locations = [l.lower() for l in (getattr(sku, "locations", None) or [])]
            if region_l and region_l not in locations:
                continue

            caps = {
                c.name: c.value
                for c in (getattr(sku, "capabilities", None) or [])
                if getattr(c, "name", None) is not None
            }

            # Zones available for this region, minus any zone-restricted ones.
            zones = set()
            for li in (getattr(sku, "location_info", None) or []):
                if str(getattr(li, "location", "") or "").lower() == region_l:
                    for z in (getattr(li, "zones", None) or []):
                        zones.add(str(z))
            restricted = False
            for r in (getattr(sku, "restrictions", None) or []):
                reason = str(getattr(r, "reason_code", "") or "").lower()
                if "notavailableforsubscription" in reason:
                    restricted = True
                if "zone" in str(getattr(r, "type", "") or "").lower():
                    ri = getattr(r, "restriction_info", None)
                    for z in (getattr(ri, "zones", None) or []):
                        zones.discard(str(z))

            result[sku.name] = {"caps": caps, "restricted": restricted, "zones": zones}
    except Exception as exc:  # noqa: BLE001 -- fail open on pagination/parse error
        _log("error iterating SKUs for %s: %s", region, exc)
        return None

    _SKU_CACHE[key] = result
    return result


def _cap_bool(caps, name):
    return str(caps.get(name, "")).strip().lower() == "true"


def _has_confidential_capability(caps):
    """True if the SKU advertises any confidential-computing capability.

    Uses a name-contains match rather than a hard-coded key so it tolerates the
    exact capability name (e.g. 'ConfidentialComputingType'); verify against a
    live resource_skus.list() for your tenant.
    """
    for name, value in caps.items():
        if "confidential" in str(name).lower() and str(value).strip():
            return True
    return False


def _reject_reason(size_name, sku_map, order, zone_data_available):
    """Return None if the size satisfies every applicable constraint, else a short
    reason string (used for both filtering and troubleshooting logs).

    Any dimension whose data is unavailable is not enforced (fail open), except
    architecture/generation/zone which are genuine deploy blockers when known.
    """
    info = sku_map.get(size_name)
    if info is None:
        return "not offered in region"
    if info["restricted"]:
        return "NotAvailableForSubscription"
    caps = info["caps"]

    # Architecture (default x64 when capability absent).
    if order.get("architecture"):
        sku_arch = str(caps.get("CpuArchitectureType", "x64")).lower()
        if sku_arch != order["architecture"]:
            return f"arch {sku_arch} != image {order['architecture']}"

    # Hyper-V generation.
    if order.get("generation"):
        gens = str(caps.get("HyperVGenerations", "") or "").upper()
        if gens and order["generation"] not in [g.strip() for g in gens.split(",")]:
            return f"gen {order['generation']} not in {gens}"

    # Security type (from the security_type_arm parameter; unset == Standard).
    sec = order.get("security_type")
    if sec == "trustedlaunch" and _cap_bool(caps, "TrustedLaunchDisabled"):
        return "TrustedLaunchDisabled"
    if sec == "confidentialvm" and not _has_confidential_capability(caps):
        return "not confidential-capable"

    # Accelerated networking (only when requested).
    if order.get("accelerated_networking") and "AcceleratedNetworkingEnabled" in caps \
            and not _cap_bool(caps, "AcceleratedNetworkingEnabled"):
        return "no AcceleratedNetworkingEnabled"

    # Encryption at host (only when requested).
    if order.get("encryption_at_host") and "EncryptionAtHostSupported" in caps \
            and not _cap_bool(caps, "EncryptionAtHostSupported"):
        return "no EncryptionAtHostSupported"

    # Availability zone (only when a zone is selected and zone data exists).
    zone = order.get("zone")
    if zone and zone_data_available and zone not in info["zones"]:
        return f"zone {zone} not in {sorted(info['zones'])}"

    # Premium / Ultra storage requires PremiumIO.
    if order.get("premium_required") and "PremiumIO" in caps and not _cap_bool(caps, "PremiumIO"):
        return "no PremiumIO"

    return None


def _size_label(size_name, sku_map):
    """Return e.g. 'Standard_B2s (2 CPU, 4 GB Memory)', falling back to the bare
    name if vCPUs/MemoryGB aren't present on the SKU (fail-open on labeling)."""
    caps = ((sku_map or {}).get(size_name) or {}).get("caps", {})
    vcpus = caps.get("vCPUs")
    memory = caps.get("MemoryGB")
    if vcpus is None and memory is None:
        return size_name

    parts = []
    if vcpus is not None:
        # vCPUs is an integer count; render without a trailing ".0".
        try:
            parts.append(f"{int(float(vcpus))} CPU")
        except (TypeError, ValueError):
            parts.append(f"{vcpus} CPU")
    if memory is not None:
        # MemoryGB can be fractional (e.g. "0.75", "1.75"); trim a whole number.
        try:
            mem = float(memory)
            mem_str = str(int(mem)) if mem.is_integer() else f"{mem:g}"
        except (TypeError, ValueError):
            mem_str = str(memory)
        parts.append(f"{mem_str} GB Memory")

    return f"{size_name} ({', '.join(parts)})" if parts else size_name


def _result(sizes, sku_map):
    """Return CloudBolt's rich options dict, mirroring the native hook's shape.

    Labels are enriched with CPU/memory from the SKU capabilities we already
    fetched (no extra API calls); values remain the bare size name.
    """
    options = [(s, _size_label(s, sku_map)) for s in sizes]
    return {
        "options": options,
        "override": True,
        "initial_value": options[0] if options else "",
    }


def get_options_list(field, control_value=None, control_value_dict=None,
                     form_data=None, form_prefix=None, **kwargs):
    """Return env-configured node sizes that Azure can actually deploy for this order."""
    cvd = _controllers(control_value_dict, kwargs)
    _log("=== get_options_list start === control_value type=%s repr=%.120r | "
         "control_value_dict keys=%s | form_prefix=%r | kwargs keys=%s",
         type(control_value).__name__, control_value, sorted(cvd.keys()),
         form_prefix, sorted(kwargs.keys()))

    env = _resolve_environment(kwargs, form_data, form_prefix)
    if env is None:
        _log("no environment resolved -> prompting to select an environment")
        return [("", "------ Select an environment first ------")]

    # Base set = sizes configured on node_size for this environment (native pattern).
    base_sizes = _env_configured_sizes(field, env)
    _log("environment=%r (id=%s) base_sizes(%d)=%s",
         getattr(env, "name", env), getattr(env, "id", None), len(base_sizes), base_sizes)
    if not base_sizes:
        return [("", "------ No node sizes configured for this environment ------")]

    rh = env.resource_handler.cast() if getattr(env, "resource_handler", None) else None
    region = (getattr(env, "node_location", "") or "").strip()

    from resourcehandlers.azure_arm.models import AzureARMHandler
    if not isinstance(rh, AzureARMHandler) or not region:
        _log("rh=%s (Azure=%s) region=%r -> NOT filtering (returning base sizes unfiltered)",
             type(rh).__name__ if rh else None, isinstance(rh, AzureARMHandler), region)
        return _result(base_sizes, {})

    sku_map = _sku_capability_map(rh, region)
    if sku_map is None:
        _log("SKU data unavailable for region=%r -> returning base sizes UNFILTERED", region)
        return _result(base_sizes, {})
    _log("region=%r sku_map has %d VM sizes", region, len(sku_map))

    # Image-derived requirements (architecture / generation).
    os_build = _resolve_os_build(cvd, control_value, form_data, form_prefix)
    _log("resolved os_build=%r (type=%s)",
         getattr(os_build, "name", os_build), type(os_build).__name__)
    if not os_build:
        _log("os_build NOT resolved -> architecture UNKNOWN (no arch filter). "
             "Most likely the os_build REGENOPTIONS dependency is not wired to node_size.")
    image_reqs = _image_requirements(rh, _get_azure_image(os_build, rh, env)) if os_build \
        else {"architecture": None, "generation": None}

    # Parameter-derived requirements (each absent -> not enforced).
    security_type = _get_param(cvd, form_data, form_prefix, kwargs, CF_SECURITY_TYPE)
    zone = _get_param(cvd, form_data, form_prefix, kwargs, CF_AVAILABILITY_ZONE)
    accel = _get_param(cvd, form_data, form_prefix, kwargs, CF_ACCELERATED_NETWORKING)
    enc_host = _get_param(cvd, form_data, form_prefix, kwargs, CF_ENCRYPTION_AT_HOST)
    storage_type = _resolve_storage_type(cvd, control_value, form_data, form_prefix)

    order = {
        "architecture": image_reqs["architecture"],
        "generation": image_reqs["generation"],
        # unset security type == Standard == no constraint
        "security_type": str(security_type).strip().lower() if security_type else None,
        "accelerated_networking": _as_bool(accel) if accel is not None else False,
        "encryption_at_host": _as_bool(enc_host) if enc_host is not None else False,
        "zone": str(zone).strip() if zone not in (None, "") else None,
        "premium_required": any(tok in str(storage_type or "").lower() for tok in _PREMIUM_STORAGE_TOKENS),
    }

    # Zone filter is meaningful only if the region actually reports zones for any size.
    zone_data_available = any(info["zones"] for info in sku_map.values())
    _log("order=%r zone_data_available=%s", order, zone_data_available)

    surviving = []
    for size in base_sizes:
        reason = _reject_reason(size, sku_map, order, zone_data_available)
        sku_arch = ((sku_map.get(size) or {}).get("caps", {}) or {}).get("CpuArchitectureType", "x64")
        if reason is None:
            surviving.append(size)
            _log("size %s: KEEP (sku CpuArchitectureType=%s)", size, sku_arch)
        else:
            _log("size %s: DROP (%s)", size, reason)

    _log("=== result === %d/%d sizes survived: %s", len(surviving), len(base_sizes), surviving)

    if not surviving:
        return [("", "No Node Sizes Available for selected options")]
    return _result(surviving, sku_map)


# ---------------------------------------------------------------------------
# SETUP NOTES
# ---------------------------------------------------------------------------
# Wire REGENOPTIONS FieldDependencies with dependent-field = node_size and
# controlling-field = each of: os_build, the storage-type CF, security_type_arm,
# availability_zone_arm, enable_accelerated_networking, encryption_at_host.
#
# It is SAFE to declare all of these on a shared plugin even though several are
# optional and absent from many forms (confirmed against platform source):
#   - If a controlling CF exists globally but isn't on a given form, the
#     dependency row is kept and node_size still regenerates; the absent
#     controller is simply omitted from control_value_dict (API) or passed as
#     None (classic form). This plugin reads every controller with .get(...).
#   - If a controlling CF doesn't exist at all, the dependency is silently
#     dropped on import (same as a malformed suffix).
# So you do NOT need to tailor the dependency set per blueprint.
#
# Value spaces (all global CFs, namespace None):
#   - security_type_arm: "TrustedLaunch" | "ConfidentialVM" (unset == Standard).
#   - availability_zone_arm: "1" | "2" | "3" (unset == no zone pin).
#   - enable_accelerated_networking / encryption_at_host: BOOL (real bools).
# Security type is authoritative from this parameter, NOT the OS image; the image
# only needs to be capable of the chosen type. CloudBolt does not pre-check
# image/size security compatibility itself -- this plugin adds that guard.
#
# Requirements still NOT enforced (add when those fields/inputs are available):
#   - Ultra disk zonal UltraSSDAvailable (beyond PremiumIO + zone).
#   - Ephemeral OS disk (EphemeralOSDiskSupported), data-disk count
#     (MaxDataDiskCount), and quota/capacity (separate Compute Usage API).