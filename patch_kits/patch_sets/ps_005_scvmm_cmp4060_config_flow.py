"""patch_kit (CMP-4060): SCVMM VMConfiguration-flow provisioning delta.

Runtime monkey-patch for commit 90c66e4 -- no source files are edited. Covers
the four units of that commit:

  U1  create_vm switches from the direct ``New-SCVirtualMachine -VMTemplate``
      clone to the VMConfiguration flow (New-SCVMConfiguration ->
      Set-SCVirtualHardDiskConfiguration -FileName -> Update-SCVMConfiguration
      -> New-SCVirtualMachine -VMConfiguration) so every template-laid disk is
      named ``<name>-disk<N>.vhdx``. Placement moves onto the config; HA is
      inherited from the template.
  U2  create_resource pre-flights the template's IsHighlyAvailable flag and
      fails fast with SCVMM_ERROR_TEMPLATE_NOT_HA instead of the raw Error
      23001 a non-HA template raises on clustered storage.
  U3  VM import (get_all_vms) filters to IsHighlyAvailable VMs only; the flag
      is surfaced by the wrapper (_get_all_vms / _get_vm add IsHighlyAvailable
      to Select-Object; _assemble_server_dict emits ``is_highly_available``)
      and popped in get_all_vms so the syncvms server_dict shape is unchanged.
  U4  provisionjob.adjust_disks routes SCVMM ``disk_<n>_size`` through the new
      SCVMMHandler.provision_disk_at_index, extending an existing template disk
      at that index instead of appending a duplicate.

Format follows ps_004: each new/changed symbol is re-defined here and bound
onto the live class. Two symbols use a different technique because a verbatim
re-definition would be fragile:

  * TechnologyWrapper._assemble_server_dict -- the commit only ADDS one key to
    a large method, so we wrap the original and augment its return value rather
    than reproduce the whole body.
  * provisionjob.adjust_disks -- a large module-level function whose only delta
    is an inserted ``elif``. It references many provisionjob module globals
    (CustomFieldValue, get_disk_index, ...), so the verbatim source is exec'd
    into the provisionjob module namespace where those globals resolve.

Depends on ps_004: create_resource (re-bound here to carry the U2 HA pre-flight)
calls ``self._reset_stale_clone_ip``, which ps_004 binds onto SCVMMHandler.
"""
import jobengine.jobmodules.provisionjob as provisionjob
from common.methods import set_progress
from infrastructure.models import Server
from resourcehandlers.scvmm import models as scvmm_models
from resourcehandlers.scvmm.models import (
    SCVMM_ERROR_BLANK_MAC_AFTER_CREATE,
    SCVMMDisk,
    SCVMMHandler,
    _handler_error,
)
from resourcehandlers.scvmm.scvmm_wrapper import (
    _LEGACY_VM_PATH,
    SCVMM_ERROR_CREATE_VM,
    TechnologyWrapper,
    _is_scvmm_not_found_error,
    _normalize_mac,
    _ps_quote_single,
    _scvmm_error,
)
from utilities.exceptions import (
    CloudBoltException,
    CommandExecutionException,
    NotFoundException,
)
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# New models module constant (U2). Set on the live module so any code that
# reads it as scvmm_models.SCVMM_ERROR_TEMPLATE_NOT_HA resolves, and referenced
# by the re-bound create_resource below.
SCVMM_ERROR_TEMPLATE_NOT_HA = "SCVMM_TEMPLATE_NOT_HIGHLY_AVAILABLE"


def patch_scvmm_config_flow():
    # ---- TechnologyWrapper (scvmm_wrapper) ------------------------------

    def _get_all_vms(self):
        """
        Use Get-SCVirtualMachine to get all VMs that are managed by our SCVMM host.
        """
        script_contents = f"Get-SCVirtualMachine -VMMServer {self.scvmm_connection.ip} | Select-Object -Property Name, Memory, ID, Status, CPUCount, OperatingSystem, VMHost, IsHighlyAvailable"
        try:
            response = self._run(script_contents)
        except CommandExecutionException:
            raise CloudBoltException(
                f"Could not locate VMs for SCVMM Host '{self.scvmm_connection.ip}'"
            )
        # _run returns None when the pipeline emits no output (no VMs, or none
        # visible to a scoped read-only role); callers iterate the result.
        if response is None:
            return []
        if isinstance(response, dict):
            response = [response]
        return response

    def _get_vm(self, server_id):
        """
        Use Get-SCVirtualMachine to get information for a single VM.
        """
        script_contents = f"Get-SCVirtualMachine -ID {server_id} | Select-Object -Property Name, Memory, ID, Status, CPUCount, OperatingSystem, VMHost, IsHighlyAvailable"
        try:
            response = self._run(
                script_contents,
                description=f"Reading core properties of VM {server_id}",
            )
        except CommandExecutionException as exc:
            if _is_scvmm_not_found_error(exc):
                raise NotFoundException(
                    f"VM with ID '{server_id}' was not found in SCVMM.",
                    object_type="Server",
                )
            raise CloudBoltException(f"Could not locate VM with ID '{server_id}'")
        return response

    _orig_assemble_server_dict = TechnologyWrapper._assemble_server_dict

    def _assemble_server_dict(self, vm_dict, cluster_name, nics, vdds, tags):
        # The commit's only change to this method is adding is_highly_available
        # to the emitted server_dict; wrap the original and augment rather than
        # reproduce the full body. Consumed by SCVMMHandler.get_all_vms to
        # import only HA VMs; popped there so it never reaches the syncvms
        # server_dict shape.
        server_dict = _orig_assemble_server_dict(
            self, vm_dict, cluster_name, nics, vdds, tags
        )
        server_dict["is_highly_available"] = bool(vm_dict.get("IsHighlyAvailable"))
        return server_dict

    def template_is_highly_available(self, template_name: str) -> bool:
        """Whether the named SCVMM template is authored Highly Available.

        The VMConfiguration deploy flow (see ``create_vm``) has no
        ``-HighlyAvailable`` on its parameter set, so HA is inherited from the
        template's hardware configuration. A non-HA template fails deploy on
        clustered storage with SCVMM Error 23001, so ``create_resource``
        pre-flights this. Get-SCVMTemplate:
        https://learn.microsoft.com/en-us/powershell/module/virtualmachinemanager/get-scvmtemplate?view=systemcenter-ps-2025
        """
        script = (
            f"Get-SCVMTemplate -Name '{_ps_quote_single(template_name)}' "
            f"-ErrorAction Stop | Select-Object -First 1 "
            f"-ExpandProperty IsHighlyAvailable"
        )
        try:
            response = self._run(
                script,
                description=f"Checking HA setting of SCVMM template '{template_name}'",
            )
        except CommandExecutionException as exc:
            raise CloudBoltException(
                f"Could not read the HA setting of SCVMM template "
                f"'{template_name}': {exc}"
            ) from exc
        return bool(response)

    def create_vm(self, name: str, template_name: str, cluster_name: str, **kwargs) -> dict:
        """Create a SCVMM VM from a template, STOPPED. Returns the VM ID.

        Deliberately does the minimum: clone the template into a stopped VM
        and return its SCVMM ID. The L2 VMNetwork bind and MAC-pool
        allocation are a SEPARATE step (``materialize_primary_mac``) so the
        caller can persist the ID the instant the VM exists. If the
        downstream MAC/bind step then fails (exhausted pool, un-bindable
        adapter), the VM is already tracked by CloudBolt and can be
        decommissioned via ``delete_vm`` instead of being orphaned in SCVMM.

        Network IP customization, Linux cloud-init seed ISO, and the final
        Start-SCVirtualMachine all move to ``apply_linux_seed_customization`` /
        ``apply_windows_static_ip_customization`` + ``power_vm`` so the
        framework's ``pre_networkconfig`` orchestration hook can affect them.

        ``-ComputerName`` STAYS here for Windows guests: SCVMM Sysprep
        specialize consumes the unattend.xml ``-ComputerName`` at first
        boot and there is no post-create SCVMM cmdlet that changes the
        Windows guest hostname. The pre-network hook cannot affect
        Windows hostname for SCVMM.

        VM generation comes from the template.

        Provisioning uses the VMConfiguration flow (New-SCVMConfiguration ->
        Set-SCVirtualHardDiskConfiguration -FileName -> Update-SCVMConfiguration
        -> New-SCVirtualMachine -VMConfiguration) so every template-laid disk is
        named explicitly ``<name>-disk<N>.vhdx`` (0-based; the config order
        leads with the OS/boot disk at index 0). The NewVmFromVmConfig parameter
        set has no ``-Path``/``-VMHost``/``-HighlyAvailable``: placement moves
        onto the config via Set-SCVMConfiguration ``-VMHost``/``-VMLocation``,
        and HA is INHERITED from the template's hardware configuration. CloudBolt
        only targets cluster shared storage (the mandatory
        ``scvmm_vm_mount_point`` is a CSV mount point / SMB3 share), where a
        non-HA VM fails to realize -- so the template must be authored Highly
        Available. ``create_resource`` pre-flights that requirement (otherwise
        the deploy fails with SCVMM Error 23001).

        kwargs:
            cpus (int): CPU count. Default 1.
            memory (int): Memory in MB. Default 512.
            is_windows (bool): Gates ``-ComputerName``. Linux guests use
                cloud-init for hostname, so the kwarg is ignored.
            vm_path (str): Storage location for the VM's files and disks,
                passed as ``Set-SCVMConfiguration -VMLocation`` (a CSV mount
                point or SMB3 share). When absent, falls back to the chosen
                host's ``VirtualMachinePath`` and then ``_LEGACY_VM_PATH``.
            network (str): Accepted but unused here -- the VMNetwork bind
                moved to ``materialize_primary_mac``. Kept so the caller can
                pass a single create-kwargs dict to both.

        Returns:
            dict: ``{"ID": <vm-guid>, "Name": <vm-name>}``.
        """
        host_name: str = self._get_best_host_for_cluster(cluster_name, template_name)
        # Caller-supplied mount point (the env's scvmm_vm_mount_point) wins; it
        # is mandatory at the handler layer. Fall back to the host's configured
        # VirtualMachinePath only when no mount point was threaded in (e.g.
        # callers outside the standard provision path).
        vm_path = (
            kwargs.get("vm_path")
            or self._get_host_vm_path(host_name)
            or _LEGACY_VM_PATH
        )

        cpus = kwargs.get("cpus", 1)
        memory = kwargs.get("memory", 512)
        # Windows: pass guest hostname via -ComputerName for Sysprep specialize.
        # Linux: cloud-init sets hostname from the seed; do NOT pass.
        computer_name_arg = ""
        if kwargs.get("is_windows") and name:
            computer_name_arg = f"-ComputerName '{_ps_quote_single(name)}' "

        # Build a VMConfiguration from the template, name every disk
        # explicitly, then deploy from the config and emit only {ID, Name}. The
        # NIC bind + MAC-pool allocation are deliberately NOT here -- see
        # materialize_primary_mac -- so create_resource can persist the ID the
        # instant the VM exists, before anything that can fail downstream.
        #
        # Placement (host + storage) is set on the config via
        # Set-SCVMConfiguration: New-SCVirtualMachine's NewVmFromVmConfig set
        # takes no -Path/-VMHost/-HighlyAvailable. HA is inherited from the
        # (required, pre-flighted) HA template. Disks are named
        # <name>-disk<N>.vhdx by config order -- which leads with the OS/boot
        # disk (config[0] deploys to SCSI 0:0, confirmed on SCVMM 2025) -- and
        # each filename is pinned so placement rating can't rename it.
        #   New-SCVMConfiguration: https://learn.microsoft.com/en-us/powershell/module/virtualmachinemanager/new-scvmconfiguration?view=systemcenter-ps-2025
        #   Set-SCVMConfiguration: https://learn.microsoft.com/en-us/powershell/module/virtualmachinemanager/set-scvmconfiguration?view=systemcenter-ps-2025
        #   Get-SCVirtualHardDiskConfiguration: https://learn.microsoft.com/en-us/powershell/module/virtualmachinemanager/get-scvirtualharddiskconfiguration?view=systemcenter-ps-2025
        #   Set-SCVirtualHardDiskConfiguration: https://learn.microsoft.com/en-us/powershell/module/virtualmachinemanager/set-scvirtualharddiskconfiguration?view=systemcenter-ps-2025
        #   Update-SCVMConfiguration: https://learn.microsoft.com/en-us/powershell/module/virtualmachinemanager/update-scvmconfiguration?view=systemcenter-ps-2025
        #   New-SCVirtualMachine (NewVmFromVmConfig): https://learn.microsoft.com/en-us/powershell/module/virtualmachinemanager/new-scvirtualmachine?view=systemcenter-ps-2025
        name_q = _ps_quote_single(name)
        script_contents = (
            f"$VMTemplate = Get-SCVMTemplate -Name "
            f"'{_ps_quote_single(template_name)}' -ErrorAction Stop; "
            f"$VMHost = Get-SCVMHost -ComputerName "
            f"'{_ps_quote_single(host_name)}' -ErrorAction Stop; "
            f"$cbConfig = New-SCVMConfiguration -VMTemplate $VMTemplate "
            f"-Name '{name_q}' -ErrorAction Stop; "
            f"$cbConfig = Set-SCVMConfiguration -VMConfiguration $cbConfig "
            f"-VMHost $VMHost -VMLocation '{_ps_quote_single(vm_path)}' "
            f"-PinVMHost $true -PinVMLocation $true -ErrorAction Stop; "
            f"$cbVhds = @(Get-SCVirtualHardDiskConfiguration "
            f"-VMConfiguration $cbConfig); "
            f"for ($cbI = 0; $cbI -lt $cbVhds.Count; $cbI++) {{ "
            f"$cbFile = '{name_q}-disk' + $cbI + '.vhdx'; "
            f"Set-SCVirtualHardDiskConfiguration -VHDConfiguration "
            f"$cbVhds[$cbI] -FileName $cbFile -PinFileName $true "
            f"-PinSourceLocation $false -PinDestinationLocation $false "
            f"-ErrorAction Stop | Out-Null; }} "
            f"Update-SCVMConfiguration -VMConfiguration $cbConfig "
            f"-ErrorAction Stop | Out-Null; "
            f"$NewVM = New-SCVirtualMachine -Name '{name_q}' "
            f"-VMConfiguration $cbConfig "
            f"{computer_name_arg}"
            f"-CPUCount {int(cpus)} -MemoryMB {int(memory)} "
            f"-ErrorAction Stop; "
            f"[PSCustomObject]@{{ ID = $NewVM.ID; Name = $NewVM.Name }}"
        )

        failure_msg = f"Could not create VM for SCVMM Host '{self.scvmm_connection.ip}'"
        try:
            response = self._run(
                script_contents,
                description=(
                    f"Deploying VM '{name}' from template '{template_name}' on "
                    f"host '{host_name}' ({int(cpus)} vCPU, {int(memory)} MB)"
                ),
            )
        except CommandExecutionException as exc:
            # Surface the PowerShell stderr in the exception so it lands in the
            # job's exception trace, not just the job log.
            ps_error = getattr(exc, "output", "") or str(exc)
            raise _scvmm_error(
                f"{failure_msg}: {ps_error}", SCVMM_ERROR_CREATE_VM
            ) from exc

        if not isinstance(response, dict):
            logger.error(
                f"SCVMM create_vm returned a non-dict response. "
                f"Type={type(response).__name__}, value={response!r}"
            )
            raise _scvmm_error(
                f"{failure_msg}: unexpected empty/non-dict response from SCVMM "
                f"(type={type(response).__name__})",
                SCVMM_ERROR_CREATE_VM,
            )

        return response

    TechnologyWrapper._get_all_vms = _get_all_vms
    TechnologyWrapper._get_vm = _get_vm
    TechnologyWrapper._assemble_server_dict = _assemble_server_dict
    TechnologyWrapper.template_is_highly_available = template_is_highly_available
    TechnologyWrapper.create_vm = create_vm

    # ---- SCVMMHandler (models) ------------------------------------------

    scvmm_models.SCVMM_ERROR_TEMPLATE_NOT_HA = SCVMM_ERROR_TEMPLATE_NOT_HA

    def get_all_vms(self):
        """Return VM dicts for every imported cluster, from one bulk inventory read.

        SCVMM exposes no per-cluster VM filter, so the wrapper fetches the whole
        inventory once (``get_all_vm_dicts`` -- a constant handful of bulk reads
        regardless of VM count) and this method keeps the VMs whose host cluster
        matches an imported cluster, filtering in Python. Previously each cluster
        re-fetched the entire inventory, so an N-cluster handler paid N full
        inventory scans per sync.
        """
        wrapper = self.get_api_wrapper()
        cluster_names = self.current_clusters()
        set_progress(f"Fetching VMs from {len(cluster_names)} imported cluster(s)")
        try:
            all_vm_dicts = wrapper.get_all_vm_dicts()
        except NotFoundException as err:
            set_progress(f"{err}. Skipping VM sync.")
            return []

        vms = []
        excluded_non_ha = 0
        for vm in all_vm_dicts:
            # get_all_vm_dicts sets "cluster" to the raw VMHost.HostCluster
            # (e.g. "prod.corp.com"); match it to an imported cluster name
            # using the same prefix rule the per-cluster path used, and
            # normalize "cluster" back to the imported name for downstream.
            host_cluster = vm.get("cluster") or ""
            matched = next(
                (c for c in cluster_names if (c + ".") in host_cluster), None
            )
            if not matched:
                continue
            # Import only highly-available VMs. CloudBolt provisions HA on
            # clustered storage; a non-HA VM there can't be managed
            # consistently. Pop the flag so it never reaches the server_dict
            # shape syncvms consumes.
            if not vm.pop("is_highly_available", False):
                excluded_non_ha += 1
                continue
            vm["cluster"] = matched
            vms.append(vm)
        if excluded_non_ha:
            set_progress(
                f"Excluded {excluded_non_ha} non-highly-available VM(s) from import"
            )
        return vms

    def create_resource(self, resource_id: int, use_template: bool) -> str:
        """Create the VM via the wrapper and persist the result on the Server.

        Non-blocking: does NOT wait for cloud-init / Sysprep. The framework
        calls ``Server.wait_for_os_readiness()`` after this returns, which
        delegates to ``SCVMMHandler.wait_for_os_readiness()`` for the wait
        and seed-ISO cleanup.

        Creation is two wrapper calls: ``create_vm`` clones a stopped VM and
        returns its ``{"ID"}``, which we persist IMMEDIATELY -- so if the
        second call (``materialize_primary_mac``, the L2 VMNetwork bind + MAC-
        pool allocation) fails, the VM is tracked by CloudBolt and
        decommissionable rather than orphaned in SCVMM. ``materialize_primary_mac``
        returns ``{"MACAddress"}``; the MAC reaches the Server's NIC row via
        ``refresh_info`` -> ``get_nics_for_vm`` ->
        ``ServerUpdater.update_network_info_for_vm``; the framework's
        ``provisionjob.create_resource_using_template`` then reads it off
        ``svr.nics.first().mac``. We verify that landing here so a blank
        MAC fails with a focused SCVMM error rather than the generic
        framework "Creation of the resource returned a blank MAC".

        Returns the Server id.
        """
        wrapper = self.get_api_wrapper()
        server: Server = Server.objects.get(id=resource_id)

        create_vm_kwargs: dict = self.get_create_resource_kwargs(server)

        # The VMConfiguration deploy flow inherits HA from the template (its
        # parameter set has no -HighlyAvailable). CloudBolt provisions onto
        # clustered storage, where a non-HA VM fails to realize (SCVMM Error
        # 23001). Fail fast with an actionable message instead of that raw error.
        template_name = create_vm_kwargs.get("template_name")
        if template_name and not wrapper.template_is_highly_available(template_name):
            raise _handler_error(
                f"SCVMM template '{template_name}' is not configured as Highly "
                f"Available. CloudBolt provisions onto clustered storage (a CSV "
                f"mount point or SMB3 share), which requires the template's "
                f"hardware configuration to have 'Make this virtual machine "
                f"highly available' enabled so the deployed VM inherits HA. "
                f"Enable it on the template and retry.",
                SCVMM_ERROR_TEMPLATE_NOT_HA,
            )

        new_vm: dict = wrapper.create_vm(**create_vm_kwargs)

        # Persist the ID before the MAC/bind step so a failure there leaves a
        # tracked, decommissionable VM instead of an SCVMM orphan.
        server.resource_handler_svr_id = new_vm.get("ID")
        server.save()

        mac_result: dict = wrapper.materialize_primary_mac(
            vm_id=server.resource_handler_svr_id,
            network_name=create_vm_kwargs.get("network", ""),
        )

        # Push CloudBolt tags as SCVMM custom properties before the refresh, so
        # the refresh_info below reads them back and reconciles them onto the
        # server's CustomFieldValues. Done here rather than in
        # wait_for_os_readiness because that method early-returns for
        # passthrough/uncustomized VMs, which would skip tagging entirely.
        self.update_tags(server)

        server.refresh_info()

        server = server.refetch()
        nic = server.nics.first()
        if not nic or not _normalize_mac(nic.mac):
            raise _handler_error(
                f"SCVMM VM '{server.resource_handler_svr_id}' was created but "
                f"its primary NIC has no MAC address after refresh_info. "
                f"materialize_primary_mac reported MACAddress="
                f"{mac_result.get('MACAddress')!r}; expected get_nics_for_vm to "
                f"surface the same value. Check that the SCVMM MAC pool has "
                f"available addresses and that the template's network adapter "
                f"is bindable to the requested VMNetwork.",
                SCVMM_ERROR_BLANK_MAC_AFTER_CREATE,
            )

        # The refresh above ran against a powered-off clone, so any IP it
        # surfaced is the template's stale IPv4Addresses, not a real runtime
        # address. Replace it with what this deploy actually expects. Bound by
        # ps_004.
        self._reset_stale_clone_ip(server)

        # The response doesn't have a task ID, so just return the server's ID
        return server.id

    def provision_disk_at_index(self, server, disk_index, disksize, cfvs):
        """Apply one ``disk_<n>_size`` provisioning parameter by index.

        If a disk already exists at ``disk_index`` -- a template-baked disk the
        clone laid down and ``refresh_info`` synced into an ``SCVMMDisk`` row --
        extend it to ``disksize`` GB rather than adding a new disk (the previous
        behavior appended a duplicate). Extension happens only when the request
        is larger; a request to shrink or match is logged and the existing
        larger disk is kept, since shrinking at provision risks data loss. When
        no disk exists at that index, a new disk is added as before.
        """
        try:
            requested = int(disksize)
        except (TypeError, ValueError):
            requested = 0
        existing = SCVMMDisk.objects.filter(
            server=server, disk_number=disk_index
        ).first()
        if existing is None:
            return self.add_disk_to_existing_server(server, disksize, cfvs)

        current = existing.disk_size or 0
        if requested > current:
            return self.extend_disk(server.id, existing, requested)
        msg = (
            f"disk_{disk_index}_size requested {requested} GB but disk "
            f"{disk_index} on {server.hostname} is already {current} GB; "
            f"keeping the larger existing disk (not shrinking)."
        )
        logger.info(msg)
        return msg

    SCVMMHandler.get_all_vms = get_all_vms
    SCVMMHandler.create_resource = create_resource
    SCVMMHandler.provision_disk_at_index = provision_disk_at_index

    # ---- provisionjob.adjust_disks (U4) ---------------------------------
    # Large module-level function; only delta is the inserted scvmm elif. Exec
    # the verbatim source into the provisionjob module namespace so its globals
    # (CustomFieldValue, get_disk_index, CloudBoltException, logger, ...)
    # resolve exactly as in source. The sole caller (create_disks_using_template
    # at provisionjob.py) looks adjust_disks up as a module global, so the
    # re-exec'd definition is what it invokes.
    exec(compile(_ADJUST_DISKS_SRC, "<scvmm-patch:adjust_disks>", "exec"),
         vars(provisionjob))

    logger.info(
        "Applied CMP-4060 SCVMM config-flow patch: 5 wrapper methods, "
        "3 model methods, 1 model constant, provisionjob.adjust_disks."
    )


# Verbatim source of provisionjob.adjust_disks with the SCVMM elif (U4).
_ADJUST_DISKS_SRC = r'''
def adjust_disks(svr, job, resource_handler):
    """
    Extend the root disk and add add'l ones based on user parameters.

    Only applies to resource handlers that support it.
    """
    prog_msg = "Adjusting disks based on provisioning parameters"
    job.set_progress(prog_msg)

    # FIXME: should disks also be adjusted when VMs are created from scratch?
    # Extend hardware resources. Filesystem-extension is
    # done in post_boot_config().
    from behavior_mapping.models import CustomFieldMapping

    disk_global_cfm_filter_set = (
        CustomFieldMapping.global_mappings_with_defaults.for_servers().filter(
            custom_field__name="disk_size"
        )
    )

    # If svr.disk_size is 0 or None
    # We should try to use any global settings for disk_size
    disk_size = svr.disk_size
    if not disk_size and disk_global_cfm_filter_set.exists():
        global_default_cfv = disk_global_cfm_filter_set.first().default
        disk_size = global_default_cfv.value

    if disk_size:
        resource_handler.extend_root_disk(svr.id, disk_size)

    if resource_handler.type_slug == "vmware":
        # Set disk_mode in case of vmware vm
        if (
            hasattr(svr, "vmware_disk_mode")
            and svr.vmware_disk_mode
            and svr.vmware_disk_mode != "persistent"
        ):
            resource_handler.modify_root_disk_mode(svr, svr.vmware_disk_mode)

    # The CFVs for additional disks (vs. the root disk, which is disk_size)
    # start with the number 1 for the 2nd disk (disk_1_size) and go from there. It's as if
    # they're 0-indexed, but we use disk_size for the 1st (root), rather than
    # disk_0_size, because if follows a different process.
    cfvs = CustomFieldValue.objects.select_related("field").filter(
        server=svr.id, field__name__regex=r"^disk_[1-9]\d*_size$"
    )
    cfvs = sorted(cfvs, key=get_disk_index)

    # If the rh does NOT support additional disks, return before attempting to add disks
    if (
        not resource_handler.can_add_disks_after_prov
        and not resource_handler.can_add_disks_at_prov
    ):
        # But if the rh handler was attempting to provision additonal disks, warn the user
        if cfvs:
            raise CloudBoltException(
                "I don't know how to add disks to {}".format(resource_handler.type_slug)
            )
        return

    for cfv in cfvs:
        disk_index = get_disk_index(cfv)
        # Add 1 because disk_1_size is the 2nd disk, and so on
        if cfv.value in (None, 0):
            prog_msg = f"Skipping disk {disk_index + 1} because it was being added with a size of {cfv.value}"
            job.set_progress(prog_msg)
            continue

        prog_msg = f"Adding disk {disk_index + 1} to server"
        job.set_progress(prog_msg)
        if resource_handler.type_slug == "vmware":
            ds_cf = "disk_{}_datastore".format(disk_index)
            dm_cf = "vmware_disk_{}_mode".format(disk_index)
            # if this attr is not set on the server, datastore will be None,
            # the resource handler knows how to handle the case where the
            # datastore is not specified
            disk_i_datastore = svr.get_value_for_custom_field(ds_cf)
            disk_i_disk_mode = svr.get_value_for_custom_field(dm_cf)
            msg = resource_handler.add_disk_to_existing_server(
                svr,
                cfv.value,
                svr.custom_field_values.all(),
                datastore=disk_i_datastore,
                disk_mode=disk_i_disk_mode,
            )
        elif resource_handler.type_slug == "vcloud_director":
            dm_cf = "vcloud_director_disk_{}_mode".format(disk_index)
            disk_i_disk_mode = svr.get_value_for_custom_field(dm_cf)
            msg = resource_handler.add_disk_to_existing_server(
                svr,
                cfv.value,
                svr.custom_field_values.all(),
                disk_mode=disk_i_disk_mode,
            )
        elif resource_handler.type_slug == "azure_arm":
            sa_cf = "disk_{}_storage_account".format(disk_index)
            # if this attr is not set on the server, storage account will be
            # None, which the RH knows how to handle
            disk_i_storage_account = svr.get_value_for_custom_field(sa_cf)

            sa_type_cf = "disk_{}_storage_account_type".format(disk_index)
            # if this attr is not set on the server, host_caching will be
            # None, which the RH knows how to handle
            disk_i_storage_account_type = svr.get_value_for_custom_field(sa_type_cf)

            host_caching_cf = "disk_{}_host_caching".format(disk_index)
            # if this attr is not set on the server, host_caching will be
            # None, which the RH knows how to handle
            disk_i_host_caching = svr.get_value_for_custom_field(host_caching_cf)
            msg = resource_handler.add_disk_to_existing_server(
                svr,
                cfv.value,
                svr.custom_field_values.all(),
                storage_account=disk_i_storage_account,
                storage_type=disk_i_storage_account_type,
                host_caching=disk_i_host_caching,
            )
        elif resource_handler.type_slug in ["aws", "aws_govcloud", "aws_china"]:
            # encrypt all disks with the same KMS key (if it exists) and encryption is True
            vol_enc_key_arn_cf = "aws_volume_encryption_key_arn"
            disk_encryption_key = svr.get_value_for_custom_field(vol_enc_key_arn_cf)

            vol_encryption_cf = f"disk_{disk_index}_encryption"
            # if this attr is not set on the server, encryption will be False
            disk_i_encryption = svr.get_value_for_custom_field(vol_encryption_cf)
            if not disk_i_encryption:
                disk_i_encryption = False
                disk_encryption_key = ""

            msg = resource_handler.add_disk_to_existing_server(
                svr,
                cfv.value,
                svr.custom_field_values.all(),
                encrypted=disk_i_encryption,
                encryption_key_arn=disk_encryption_key,
            )
        elif resource_handler.type_slug == "ovirt":
            disk_access_mode_cf = "ovirt_disk_{}_access_mode".format(disk_index)
            disk_access_mode = svr.get_value_for_custom_field(disk_access_mode_cf)

            disk_interface_name_cf = "ovirt_disk_{}_interface_name".format(disk_index)
            disk_interface_name = svr.get_value_for_custom_field(disk_interface_name_cf)

            disk_storage_class_cf = "ovirt_disk_{}_storage_class".format(disk_index)
            disk_storage_class = svr.get_value_for_custom_field(disk_storage_class_cf)

            msg = resource_handler.add_disk_to_existing_server(
                svr,
                cfv.value,
                svr.custom_field_values.all(),
                disk_interface_name=disk_interface_name,
                disk_access_mode=disk_access_mode,
                disk_storage_class=disk_storage_class,
            )
        elif resource_handler.type_slug == "scvmm":
            # SCVMM templates can carry their own disks. When disk_<n>_size
            # targets an index a template disk already occupies, extend that
            # disk instead of appending a duplicate (never shrink).
            msg = resource_handler.provision_disk_at_index(
                svr, disk_index, cfv.value, svr.custom_field_values.all()
            )
        else:
            msg = resource_handler.add_disk_to_existing_server(
                svr, cfv.value, svr.custom_field_values.all()
            )
        logger.info(msg)
'''
