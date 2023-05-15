from resourcehandlers.vmware.models import VsphereResourceHandler
from xui.nsxt.xui_utilities import check_for_nsxt, NSXTXUIAPIWrapper


# Query the NSX-T manager to get a list of all security tags available
def get_options_list(field, server=None, environment=None, **kwargs):
    if environment and environment.resource_handler:
        rh = environment.resource_handler.cast()
        if not check_for_nsxt(rh):
            return None

        options = []

        # Get tags from API
        nsx = NSXTXUIAPIWrapper(rh)
        tags = nsx.get_all_security_tags()

        # Attach tags to options list
        for tag in tags:
            options.append((tag, tag))

        # Make sure list is not empty
        if len(options) == 0:
            return [(None, "No security tags available")]

        return options
