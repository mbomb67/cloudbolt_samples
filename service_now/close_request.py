"""
Hook to close a Service Catalog Request Record for Order an order when complete.
This hook should be created as an Orchestration Action at the Post-Order
Execution hook point
"""

import json
from common.methods import set_progress
from itsm.servicenow.models.servicenow_itsm import ServiceNowITSM
from utilities.logger import ThreadLogger

logger = ThreadLogger("Service Now Order Close")


def run(order, *args, **kwargs):
    """
    Grabs the Request ID, then sets the ServiceNow Request to Closed Complete
    on success or Closed Incomplete (if Job fails). Also writes IP address and
    Hostname to the Special Instructions Field
    """
    logger.info('Closing ServiceNow Request')

    if not order:
        return False
    try:
        bpoi = get_bp_order_item(order)
        request_id = bpoi.get_cfv_for_custom_field(
            "snow_order_submit_sys_id").value
        instructions = create_instructions(order, bpoi)
    except Exception as e:
        try:
            logger.debug(f'service_now: BPOI not found with SNow ID trying '
                         f'SMOI. Error: {e}')
            smoi = get_smoi(order)
            request_id = smoi.custom_field_values.get(
                field__name='snow_order_submit_sys_id'
            ).value
            instructions = None
        except Exception as e2:
            logger.debug(f'service_now: SMOI not found with SNow ID. Error: '
                         f'{e2}')
            logger.warn("ServiceNow Request ID not on this order, continuing")
            return "SUCCESS", "", ""
    result = close_request(order, request_id, instructions)
    return result


def create_instructions(order, bp_order_item):
    # Gather IP address, hostname for Servers, all Attribute type params from
    # Resource
    resource = bp_order_item.get_resource()
    servers = bp_order_item.get_servers()
    instructions = ''
    if resource:
        attributes = get_resource_attributes(resource)
        if attributes:
            instructions += f"Resource: {resource.name}. Fields: "
            instructions += f'{attributes}\r\n'
    if servers:
        for server in servers:
            instructions += f'Server: {server.hostname}, ' \
                            f'IP Address: {server.ip}\r\n'
    return instructions


def get_resource_attributes(resource):
    attributes_string = ''
    attributes = resource.attributes.all()
    for attribute in attributes:
        if attribute.field.show_as_attribute:
            field = attribute.field.name
            value = attribute.value
            attributes_string += f'{field}: {value}, '
    return attributes_string


def get_bp_order_item(order):
    # Find the order_item that has a reference to a servicenow request sys_id
    order_item = order.orderitem_set.filter(
        blueprintorderitem__isnull=False,
        blueprintorderitem__custom_field_values__field__name = 'snow_order_submit_sys_id'
    ).first()
    if not order_item:
        raise Exception(f'Blueprint order item could not be found')
    bp_order_item = order_item.cast()
    return bp_order_item


def get_smoi(order):
    # Find the order_item that has a reference to a servicenow request sys_id.
    # Even if there are multiple SMOIs on an order, they all have the same
    # SNow sys id - so only grabbing the first one.
    order_item = order.orderitem_set.filter(
        servermodorderitem__isnull=False,
        servermodorderitem__custom_field_values__field__name='snow_order_submit_sys_id'
    ).first()
    if not order_item:
        raise Exception(f'ServerMod order item could not be found')
    smoi = order_item.cast()
    return smoi


def close_request(order, request_id, instructions):
    snowitsm = ServiceNowITSM.objects.first()
    wrapper = snowitsm.get_api_wrapper()
    base_url = wrapper.service_now_instance_url.replace("/login.do", "")
    request_url = f"{base_url}/api/now/table/sc_request/{request_id}"
    request_state, state, stage = get_state(order)
    data = {"request_state": request_state,  #str
            "state": state,  # int
            "stage": stage,  # workflow, accepts string
            }

    if instructions:
        data["special_instructions"] = instructions

    logger.info(f'SNOW PAYLOAD: {data}')
    headers = {'Accept': 'application/json',
               'Content-Type': 'application/json'}

    json_data = json.dumps(data)

    try:
        raw_response = wrapper.service_now_request(method="PUT",
                                                   url=request_url,
                                                   headers=headers,
                                                   body=json_data)
        raw_response.raise_for_status()
        response = raw_response.json()
        logger.info(f'response: {response}')
        set_progress(f'ServiceNow Request: {request_id} has been closed')
        return "", "", ""
    except Exception as e:
        return "FAILURE", f"Exception: {e}", ""


def get_state(order):
    status = order.status
    if status == 'FAILURE':
        state = ('closed_incomplete', 4, 'closed_incomplete')
    elif status == 'SUCCESS':
        state = ('closed_complete', 3, 'closed_complete')
    elif status == 'DENIED':
        state = ('closed_rejected', 3, 'closed_complete')
    else:
        raise Exception(f'Order status not supported. Status: {status}')
    return state

