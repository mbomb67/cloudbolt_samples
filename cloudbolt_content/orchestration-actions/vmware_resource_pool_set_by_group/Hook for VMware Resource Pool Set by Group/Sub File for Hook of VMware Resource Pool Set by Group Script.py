"""
Reads a parameter from the CloudBolt Group - 'group_vmware_resourcepool'
to set the VMware Resource Pool for the request
"""
import json
import requests

from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, server=None, **kwargs):
    rp_name = server.get_value_for_custom_field("group_vmware_resourcepool")
    if not rp_name:
        logger.info(f'group_vmware_resourcepool was not set on server '
                    f'{server.hostname}. exiting')
        return "", "", ""
    set_progress(f'Setting VMware Resource Pool to {rp_name}')
    server.vmware_resourcepool = rp_name
    server.save()
    return "SUCCESS", "", ""
