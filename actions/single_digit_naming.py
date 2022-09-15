import json

import html

from common.methods import uniquify_hostname_with_padding
from orders.models import BlueprintOrderItem
from utilities.logger import ThreadLogger
from django.template import Template, Context


logger = ThreadLogger(__name__)


def run(job=None, server=None, **kwargs):
    if not server:
        return "", "", ""
    logger.info(f"kwargs: {kwargs}")
    template = "{{template}}"
    char_to_replace = "{{char_to_replace}}"
    template = Template(template)
    context = {
        "resource": server.resource,
        "environment": get_environment_from_job(job),
        "group": server.group,
        "server": server,
    }
    context = Context(context)
    rendered_template = html.unescape(template.render(context))
    hostname = uniquify_hostname_with_padding(rendered_template, char_to_replace)
    logger.info(f"New Hostname: {hostname}")
    server.hostname = hostname
    server.save()
    return "SUCCESS", hostname, ""


def get_environment_from_job(job):
    return job.order_item.provisionserverorderitem.environment
