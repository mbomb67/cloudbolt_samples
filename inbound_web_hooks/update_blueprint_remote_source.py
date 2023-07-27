"""
This plugin will update the Blueprint Remote Source for a given Blueprint in
CloudBolt.

Parameters:
    blueprint_id: The ID of the Blueprint to update - this should be the numeric
        ID of the Blueprint in CloudBolt
    remote_source_url: The URL of the new Remote Source to use for the Blueprint
"""
from common.methods import set_progress
from servicecatalog.models import ServiceBlueprint


def inbound_web_hook_post(*args, parameters={}, **kwargs):
    set_progress(
        f"This message will show up in CloudBolt's application.log. args: "
        f"{args}, kwargs: {kwargs}, parameters: {parameters}"
    )
    blueprint_id = parameters.get("blueprint_id", None)
    remote_source_url = parameters.get("remote_source_url", None)
    bp = ServiceBlueprint.objects.get(id=blueprint_id)
    bp.remote_source_url = remote_source_url
    bp.save()

    return (
        {
            "message": "Successfully updated the Blueprint Remote Source",
            "result": f"Blueprint ID: {blueprint_id} updated to use Remote "
                      f"Source URL: {remote_source_url}",
        }
    )
