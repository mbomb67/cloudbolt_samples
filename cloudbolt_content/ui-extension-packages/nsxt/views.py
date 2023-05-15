from django.contrib import messages
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import reverse
from django.utils.translation import ugettext as _
from extensions.views import TabExtensionDelegate, tab_extension
from infrastructure.models import Server
from network_virtualization.models.network_virtualization_resource_handler_mapping import (
    NetworkVirtualizationResourceHandlerMapping,
)
from resourcehandlers.models import ResourceHandler
from utilities.decorators import dialog_view
from xui.nsxt.forms import NSXTagForm
from xui.nsxt.xui_settings import TEMPLATE_DIR
from xui.nsxt.xui_utilities import check_for_nsxt, NSXTXUIAPIWrapper



class NSXTagTabDelegate(TabExtensionDelegate):
    def should_display(self):
        try:
            rh = self.instance.resource_handler
            if check_for_nsxt(rh):
                return True
        except Exception as e:
            return False


class NSXTRHTagTabDelegate(TabExtensionDelegate):
    def should_display(self):
        # check for nsx-t endpoints
        if check_for_nsxt(self.instance):
            return True
        return False


@dialog_view
def add_security_tag(request, server_id):

    server = Server.objects.get(id=server_id)

    if request.method == "POST":

        form = NSXTagForm(request.POST, server=server)
        if form.is_valid():
            success, msg = form.save()
            if success:
                messages.success(request, msg)
            else:
                messages.warning(request, msg)
            return HttpResponseRedirect(reverse("server_detail", args=[server.id]))

    else:
        form = NSXTagForm(server=server)

    return {
        "use_ajax": True,
        "form": form,
        "title": "Add Security Tag to Server",
        "action_url": "/add_security_tag/{s_id}/".format(s_id=server.id),
        "submit": _("Add NSX Security Tag"),
    }


@dialog_view
def remove_security_tag(request, server_id, tag_name):
    """
    Remove a given tag from a virtual machine in NSX-T and CustomFieldValue on related Server object
    """

    server = Server.objects.get(id=server_id)
    tag = server.custom_field_values.get(str_value=tag_name)

    if request.method == "POST":
        nsx = NSXTXUIAPIWrapper(server.resource_handler)
        # Get the external_ID of the server to pass into the
        external_id = nsx.get_external_id(server)

        nsx.remove_tag_from_vm(tag.value, external_id)
        server.custom_field_values.remove(tag)

        msg = _("NSX-T Security Tag '{tag}' removed from server '{server}'")
        messages.info(request, msg.format(tag=tag_name, server=server.hostname))

        return HttpResponseRedirect(reverse("server_detail", args=[server.id]))

    else:
        content = _("Are you sure you want to remove '{tag}' tag?").format(
            tag=tag.display_value
        )

        return {
            "title": _("Remove security tag?"),
            "content": content,
            "use_ajax": True,
            "action_url": "/remove_security_tag/{s_id}/tag/{t_id}/".format(
                s_id=server_id, t_id=tag_name
            ),
            "submit": _("Remove"),
        }


@tab_extension(model=Server, title="NSX-T Server Tags", delegate=NSXTagTabDelegate)
def nsxt_tags_tab(request, obj_id):
    """
    Given a request, check if the Server should display an NSX-T tab
    """
    server = Server.objects.get(id=obj_id)

    tags = server.custom_field_values.filter(field__name="nsxt_tag")

    # Check if the server exists in NSX-T
    nsx = NSXTXUIAPIWrapper(server.resource_handler)
    nsxt_server = nsx.get_external_id(server)

    return render(
        request,
        f"{TEMPLATE_DIR}/nsxt_server_tab.html",
        dict(server=server, tags=tags, nsxt_server=nsxt_server),
    )


@tab_extension(model=ResourceHandler, title="NSX-T Tags", delegate=NSXTRHTagTabDelegate)
def nsx_tags_tab(request, obj_id):
    """
    Given a request, check if the ResourceHandler should display an NSX-T tab
    """
    rh = ResourceHandler.objects.get(id=obj_id)
    nv = NetworkVirtualizationResourceHandlerMapping.objects.get(
        resource_handler_id=rh.id
    )
    nsx = NSXTXUIAPIWrapper(rh)
    tags = nsx.get_all_security_tags()

    return render(
        request,
        f"{TEMPLATE_DIR}/nsxt_rh_tab.html",
        dict(tags=tags, nv=nv),
    )
