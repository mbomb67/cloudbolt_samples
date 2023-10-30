"""
This WebHook supports returning a list of Service Item Names from a CloudBolt
Blueprint

Required Inputs:
- blueprint_id: ID of the CloudBolt Blueprint
"""
from django.db.models import Q

from common.methods import set_progress
from servicecatalog.models import ServiceBlueprint
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def inbound_web_hook_get(*args, parameters={}, **kwargs):
    """
    Use this method for operations that are read-only and do not change anything
    in CloudBolt or the environment.
    """
    blueprint_id = parameters.get("blueprint_id", None)
    if not blueprint_id:
        raise ValueError("Missing required input. Please provide blueprint_id.")
    results = get_blueprint_service_item_names(blueprint_id)
    logger.debug(f"Results: {results}")
    return {"options": results}


def get_blueprint_service_item_names(blueprint_id):
    bp = ServiceBlueprint.objects.get(id=blueprint_id)
    return [si.name for si in get_supported_sis_for_blueprint(bp)]


def get_supported_sis_for_blueprint(bp):
    # Each Blueprint should have matching service items for each of the
    # Deployment resources. Really the only two valid sub-resources are
    # Servers and Blueprints.
    return bp.serviceitem_set.filter(
        Q(blueprintserviceitem__isnull=False) |
        Q(provisionserverserviceitem__isnull=False)
    )
