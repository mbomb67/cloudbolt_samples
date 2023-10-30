"""
This WebHook supports returning a list of vRA Projects

Required Inputs:
- vra_connection: ID of the vRA Connection Info
"""
from common.methods import set_progress
from vra.vra8_connection import VRealizeAutomation8Connection
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def inbound_web_hook_get(*args, parameters={}, **kwargs):
    """
    Use this method for operations that are read-only and do not change anything
    in CloudBolt or the environment.
    """
    vra_connection = parameters.get("vra_connection", None)
    if not vra_connection:
        raise ValueError("Missing required inputs. Please provide "
                         "vra_connection.")
    vra = VRealizeAutomation8Connection(vra_connection)
    results = [{"id": r[0], "name": r[1]} for r in vra.get_project_options()]
    return {"options": results}
