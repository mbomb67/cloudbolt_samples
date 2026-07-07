from infrastructure.models import Server
from resourcehandlers.scvmm.models import SCVMMHandler, _handler_error
from resourcehandlers.scvmm.scvmm_wrapper import (
    TechnologyWrapper,
    _normalize_mac,
)
from utilities.exceptions import CloudBoltException, NotFoundException
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

SCVMM_ERROR_BLANK_MAC_AFTER_CREATE = "SCVMM_BLANK_MAC_AFTER_CREATE"

def patch_stale_clone():
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
        # address. Replace it with what this deploy actually expects.
        logger.info(
            f"Getting ready to reset stale template IP {server.ip} on "
            f"{server.hostname} to expected value for this deploy."
        )
        self._reset_stale_clone_ip(server)

        # The response doesn't have a task ID, so just return the server's ID
        return server.id

    def _reset_stale_clone_ip(self, server: Server) -> None:
        """Reset the IP the pre-boot refresh scraped off the template.

        ``create_resource`` refreshes a freshly-cloned, powered-off VM only
        to surface its primary NIC and MAC. That refresh also reads the
        template's stale generalize-time ``IPv4Addresses`` onto ``server.ip``
        and the primary NIC (via ``set_server_ip_from_first_nic``), which
        would trip the static-IP verifier in
        ``provisionjob.post_rsrc_creation_config``: a DHCP deploy has no
        expected IP, and a static deploy expects ``sc_nic_0_ip``, not the
        template's old address. Overwrite it with the value this deploy
        expects; the post-boot refresh records the real runtime IP.

        Both the primary NIC and ``server.ip`` are reset -- a later
        ``add_nics_to_server`` NIC save would otherwise re-promote a stale NIC
        IP back onto ``server.ip`` through ``set_server_ip_from_first_nic``.
        """
        try:
            mode = self._resolve_nic_ip_mode(server)
        except CloudBoltException:
            # A static-without-IP misconfig should have failed earlier in the
            # clone; treat an unresolvable mode as "no expected IP" rather than
            # leaving the stale template value in place.
            mode = "dhcp"

        expected = ""
        if mode == "static":
            expected = (getattr(server, "sc_nic_0_ip", None) or "").strip()

        nic = server.nics.first()
        if nic is not None and (nic.ip or "") != expected:
            logger.info(
                f"Resetting stale template NIC IP {nic.ip} on {server.hostname} "
                f"to expected value {expected!r} for this deploy."
            )
            nic.ip = expected
            nic.save()
        if (server.ip or "") != expected:
            logger.info(
                f"Resetting stale template IP {server.ip} on {server.hostname} "
                f"to expected value {expected!r} for this deploy."
            )
            server.ip = expected
            server.save()

    SCVMMHandler.create_resource = create_resource
    SCVMMHandler._reset_stale_clone_ip = _reset_stale_clone_ip