"""
This WebHook supports returning a list of Service Item Names from a CloudBolt
Blueprint

Required Inputs:
- blueprint_id: ID of the CloudBolt Blueprint
"""
from accounts.models import Group
from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def inbound_web_hook_get(*args, parameters={}, **kwargs):
    """
    Use this method for operations that are read-only and do not change anything
    in CloudBolt or the environment.
    """
    results = [{"id": g.id, "name": g.name} for g in Group.objects.all()]
    return {"options": results}


