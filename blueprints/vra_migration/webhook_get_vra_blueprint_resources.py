"""
This WebHook supports returning a list of resource names for a vRA Blueprint

Required Inputs:
- vra_connection: ID of the vRA Connection Info
- blueprint_id: ID of the vRA Blueprint
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
    blueprint_id = parameters.get("blueprint_id", None)
    if not vra_connection or not blueprint_id:
        return {"error": "Missing required inputs. Please provide "
                         "vra_connection and blueprint_id."}
    vra = VRealizeAutomation8Connection(vra_connection)
    results = get_blueprint_resource_names(vra, blueprint_id)
    logger.debug(f"Results: {results}")
    return {"options": results}


def get_blueprint_resource_names(vra, blueprint_id):
    content = vra.get_blueprint_content_as_dict(blueprint_id)
    if not content:
        return {"error": "Could not get content for blueprint with ID "
                         "{}".format(blueprint_id)}
    resources = content.get("resources", [])
    logger.debug(f"Resources: {resources}")
    return [r for r in resources.keys()]
