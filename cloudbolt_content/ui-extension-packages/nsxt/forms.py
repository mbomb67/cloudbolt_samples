from common.fields import form_field_for_cf
from common.forms import C2Form
from django import forms
from django.utils.translation import ugettext as _
from django.utils.translation import ugettext_lazy as _lazy
from infrastructure.models import CustomField
from orders.models import CustomFieldValue
from utilities.logger import ThreadLogger
from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper

logger = ThreadLogger(__name__)


class NSXTagForm(C2Form):
    def __init__(self, *args, **kwargs):
        self.server = kwargs.pop("server")
        tag_field = CustomField.objects.filter(
            name="nsxt_tag",
        ).first()
        if not tag_field:
            logger.debug("Custom Value nsxt_tag does not exist, creating now")
            from .xui_utilities import setup_nsx_tags

            tag_field = setup_nsx_tags()

        self.tag_field = tag_field
        logger.debug(f"Using data {tag_field} to generate tag")

        super(NSXTagForm, self).__init__(*args, **kwargs)
        try:
            logger.debug(f"Attempting to create tag now")
            self.fields["nsxt_tag"] = form_field_for_cf(
                tag_field, server=self.server, environment=self.server.environment
            )
        except Exception as e:
            logger.debug(f"an error has occurred {e}")

    def save(self):
        tag_name = self.cleaned_data.get("nsxt_tag")
        server = self.server
        nsx = NSXTXUIAPIWrapper(server.resource_handler)
        external_id = nsx.get_external_id(server)
        nsx.add_tag_to_vm(tag_name, external_id)

        cfv, __ = CustomFieldValue.objects.get_or_create(
            field=self.tag_field, value=tag_name
        )
        server.custom_field_values.add(cfv)

        msg = f"Added tag '{tag_name}' to '{server.hostname}'"

        return True, msg
