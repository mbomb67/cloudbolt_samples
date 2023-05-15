"""
This is a post provision action to work with NSX Tagging
This action will check the domain of a deployed resource and set 
the a parameter domain_tag on the server

To utilize this action:
    - Navigate to admin > Orchestration actions
    - Apply as a new action to the Pre-Create resource orchestration action


Created by Bryce Swarm (CloudBolt Software)
"""

import json

from infrastructure.models import CustomField
from orders.models import CustomFieldValue
from resourcehandlers.vmware.models import VsphereResourceHandler
from xui.nsxt.xui_utilities import check_for_nsxt, NSXTXUIAPIWrapper


def run(job, server=None, *args, **kwargs):

    tag_field = CustomField.objects.filter(
        name="nsxt_domain_tag", namespace__name="nsxt_tag"
    ).first()
    if not tag_field:
        setup_domain_param()

    if server:
        environment = server.environment
        rh = environment.resource_handler.cast()

        # Proceed only if the environment is NSX-T configured
        if check_for_nsxt(rh):

            domain = server.domain or server.dns_domain
            nsx = NSXTXUIAPIWrapper(rh)
            tags = nsx.get_domain_tags()

            for tag in tags:
                if domain.upper() in tag:
                    domain_tag = tag
                    break

            cfv, _ = CustomFieldValue.objects.get_or_create(
                field__name="nsxt_domain_tag", value=domain_tag
            )
            server.custom_field_values.add(cfv)
            server.nsxt_domain_tag = cfv.value
            server.save()

        return "", "", ""


def setup_domain_param():
    from c2_wrapper import create_custom_field

    nsxt_tag_cf = {
        "name": "nsxt_domain_tag",
        "label": "NSX-T Domain Tag",
        "type": "STR",
    }
    create_custom_field(**nsxt_tag_cf)
