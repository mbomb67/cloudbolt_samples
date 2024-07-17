"""
A CloudBolt Plug-in that injects additional values in to the blueprint item
arguments that can then be referenced in a Terraform Variable Map
"""
from c2_wrapper import create_custom_field, create_custom_field_value
from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(order=None, job=None, resource=None, **kwargs):
    logger.debug(f'kwargs: {kwargs}')
    logger.debug(f'order: {order}')
    boi = order.orderitem_set.filter(
        blueprintorderitem__isnull=False).first().cast()
    bia = boi.blueprintitemarguments_set.first()
    if not bia:
        return "FAILURE", "", "No BlueprintItemArgument found"

    add_cfv_to_bia(bia, "azure_storage_container_name",
                           "Storage Container Name",
                           "{{storage_container_name}}", True)
    return "SUCCESS", "", ""


def add_cfv_to_bia(bia, field_name, field_label, value,
                           remove_existing_values=False):
    """
    Add a custom field value to a BlueprintItemArgument
    """
    field = create_custom_field(field_name, field_label, "STR")
    if remove_existing_values:
        bia_cfvs = bia.custom_field_values.filter(field=field)
        for bia_cfv in bia_cfvs:
            bia.custom_field_values.remove(bia_cfv)
    cfv = create_custom_field_value(field.name, value)
    bia.custom_field_values.add(cfv)
    bia.save()
    logger.info(f"Added {field_name}={value} to BIA ID: {bia.id}")
    return None
