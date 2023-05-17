# Action made to apply NSX-V Tags to provisioned servers
# Author: Bryce Swarm (CloudBolt)
# Author: jason.andrews@state.mn.us api related edits (1-21-2022)

from common.methods import set_progress
from utilities.logger import ThreadLogger
from xui.nsxt.xui_utilities import add_tag_to_vm, check_for_nsxt, get_external_id

logger = ThreadLogger(__name__)
# logger.info("")


def run(job, *args, **kwargs):

    server = kwargs.get("server")
    rh = server.resource_handler.cast()

    if check_for_nsxt(rh):

        # Skip if remote site parameter is true
        nsxskip = bool(server.get_value_for_custom_field("mnit_remote_site"))
        if nsxskip:
            set_progress(
                "Remote site environment detected: Skippping NSX security group assignments"
            )
            return (
                "SUCCESS",
                "Remote site environment detected: Skippping NSX security group assignments",
                "",
            )

        # Skip if AVS parameter is true - temporary until nsx use cases present themselves
        avsskip = bool(server.get_value_for_custom_field("mnit_avs"))
        if avsskip:
            set_progress(
                "AVS vSphere detected: Skippping NSX security group assignments"
            )
            return (
                "SUCCESS",
                "AVS vSphere detected: Skippping NSX security group assignments",
                "",
            )

        # Get the external ID of the server
        external_id = get_external_id(server, rh)

        if server.nsxt_security_tag:
            add_tag_to_vm(rh, server.nsxt_security_tag, external_id)

        if server.os_family.get_base_name() == "Windows":
            if server.nsxt_domain_tag:
                add_tag_to_vm(rh, server.nsxt_domain_tag, external_id)

        return "", "", ""
    return "", "", ""
