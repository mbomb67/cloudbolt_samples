"""
Hook to add Service Catalog Request Record for Order when submitted.
This hook should be created as an Orchestration Action at the Order Submission
hook point
"""

import json
import requests
from django.contrib.auth.models import User
from django.db.models.query import QuerySet

from common.methods import set_progress
from costs.models import CustomFieldRate
from itsm.servicenow.models.servicenow_itsm import ServiceNowITSM
from jobs.models import Job
from orders.models import get_current_time, ActionJobOrderItem, \
    BlueprintOrderItem, ServerModOrderItem, ProvisionServerOrderItem
from utilities.logger import ThreadLogger
from urllib.parse import urlencode

logger = ThreadLogger("Service Now Order Submit")


def run(order, *args, **kwargs):
    """
    Creates a new service catalog request entry which will need to get approved.
    :param order: Order object
    """
    logger.debug('service_now: in SNOW Approval integration.')
    logger.debug(f'service_now: kwargs: {kwargs}')

    if not order:
        return False
    bpoi, smois = get_order_items(order)
    if not bpoi and not smois:
        logger.info(f'service_now: ServiceNow approval is only configured for '
                    f'BluePrintOrderItem, and ServerModOrderItem types. '
                    f'Auto-approving order because none of these types were '
                    f'included')
        order.approve()
        return "SUCCESS", "", ""
    snowitsm = ServiceNowITSM.objects.first()
    wrapper = snowitsm.get_api_wrapper()
    base_url = wrapper.service_now_instance_url.replace("/login.do", "")
    sysid_for_req_by, sysid_for_req_for = get_sysid_for_users(snowitsm,
                                                              base_url, order)

    # CloudBolt can either have a single Blueprint Order Item submitted in a
    # single order, or one (or more) Server Mod Order Items - these need to be
    # addressed independently
    if bpoi:
        oi = bpoi
    elif smois:
        oi = smois
    result, approval_status = create_service_request(
        wrapper, base_url, order, sysid_for_req_by, sysid_for_req_for,
        oi)
    if approval_status == "approved":
        approve_order(order)

    return result


def lookup_ci(table_name=None, ci_name=None, ci_value=None, ci_query=None,
              base_url=None, return_ci='sys_id', sysparm_query=True,
              conn=None):
    '''
        ex.
        table_name = 'ci_cmdb_server'
        ci_name = 'asset_tag'
        ci_value = '421e19fe-5920-4ae9-75be-4646430d6772'
        return_ci = (str) or (list) (str for 1 value, list for multiple values)
                    ex. 'sys_id' or ['sys_id', 'email']
        Query servicenow with a table, and looks for a CI that has
        the field(ci_name) with the value(ci_value) and returns the sys_id for that
        CI.

        Optionally, you can pass multiple filters in the ci_query parameter as a
            dictionary...

            i.e. {'column1': 'column_value', 'column2': 'some other value'}
            ...if there is more than one filter field to query with

        If it doesn't find a record that matches the filter passed in,
           it returns None
    '''
    ci_value_data = None
    if ci_query:
        query = urlencode(ci_query)
    else:
        prefix = "sysparm_query"
        if not sysparm_query:
            query = urlencode({ci_name: ci_value})
        else:
            query = urlencode({prefix: f"{ci_name}={ci_value}"})

    url = base_url + f"/api/now/table/{table_name}?{query}"
    # print(f'lookup_ci - url: {url}')
    response = requests.get(
        url=url,
        auth=(conn.service_account, conn.password),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json"
        },
        timeout=5.0
    )
    # print(f'response = {response.text}')

    try:
        # if a list of values are sent for return, then populate a dictionary
        if isinstance(return_ci, list):
            ci_value_data = {}
            for item in return_ci:
                ci_value_data[item] = response.json()["result"][0][item]
        else:
            if return_ci == "*":
                # return everything we got back
                ci_value_data = response.json()["result"][0]
            else:
                ci_value_data = response.json()["result"][0][return_ci]
    except Exception:
        pass

    return ci_value_data


def get_order_items(order):
    bpoi, smois = None, None
    bpoi = get_bp_order_item(order)
    if not bpoi:
        smois = get_server_mod_order_items(order)
    return bpoi, smois


def get_bp_order_item(order):
    order_items = order.orderitem_set.all()
    for oi in order_items:
        real_oi = oi.cast()
        oi_type = type(real_oi)
        if oi_type == BlueprintOrderItem:
            return real_oi
    return None


def get_server_mod_order_items(order):
    order_items = order.orderitem_set.all()
    smois = []
    for oi in order_items:
        real_oi = oi.cast()
        oi_type = type(real_oi)
        if oi_type == ServerModOrderItem:
            smois.append(real_oi)
    return smois


def create_description_and_rate(order, oi):
    rate = order.get_rate()
    logger.info(f'service_now: oi type: {type(oi)}')
    if type(oi) == BlueprintOrderItem:
        item_descriptions = []
        description = f'Blueprint request for Blueprint: ' \
                      f'{order.blueprint.name}\r\n Order: {order.id}\r\n'
        item_count = 1
        for bpia in oi.blueprintitemarguments_set.all():
            cfvs = get_clean_custom_field_values(bpia)
            si = bpia.service_item
            if si.real_type.name == 'provision server service item':
                rate = update_rate_for_bpia(bpia, rate, cfvs)
                # Gather data for Server Service Item
                rh = bpia.environment.resource_handler
                item_description = f'Item {item_count}: {si.name}, Type: Server,' \
                                   f' Resource handler: {rh.name}, Environment: ' \
                                   f'{bpia.environment}, OS Build: ' \
                                   f'{bpia.os_build.name} '
            else:
                # Gather data for any other type
                item_description = f'Item {item_count}: {si.name}'
            if cfvs:
                cfv_list = [f'{key}: {cfvs[key]}' for key in cfvs.keys()]
                item_description += ', '
                item_description += ', '.join(cfv_list)
            item_descriptions.append(item_description)
            item_count += 1

        for item in item_descriptions:
            description += f'  - {item}\r\n'
    elif type(oi) == list:
        description = f'Request for Server Modification(s)\r\n Order: ' \
                      f'{order.id}\r\n'
        rate = 0
        for smoi in oi:
            rate += smoi.get_rate()
            description += f'\t> Server: {smoi.server.hostname}\r\n' \
                           f'\t   Modifications:\r\n'
            deltas = smoi.delta()
            for key in deltas.keys():
                description += f'\t\t- {key}: {deltas[key]}\r\n'
    return description, rate


def update_rate_for_bpia(bpia, rate, cfvs):
    # CPU and Mem values in preconfigurations are not working in CB - this will
    # find if the bpia has a preconfig using cpu_cnt or mem and update rates
    supported_types = ["cpu_cnt", "mem_size"]
    preconfig_values = bpia.preconfiguration_values.all()
    for preconfig_value in preconfig_values:
        for cfv in preconfig_value.get_cfv_manager().all():
            if cfv.field.name in supported_types:
                env = bpia.environment
                # First check if a rate is set for the field/env
                cfr = get_cfr(env, cfv)
                if not cfr:
                    logger.warning(f"service_now: No rates were found for "
                                   f"field: {cfv.field.name}. Continuing")
                    continue
                cf_rate = cfr.rate
                cfv_value = cfv.value
                quantity = cfvs.get("quantity") if cfvs.get("quantity") else 1
                rate += (cf_rate * cfv_value * quantity)
    return rate


def get_cfr(env, cfv):
    cfr = CustomFieldRate.objects.filter(
        environment=env, custom_field=cfv.field
    )
    if not cfr:
        # Then get the global rate for the field if env rate not
        # set
        cfr = CustomFieldRate.objects.filter(
            environment=None, custom_field=cfv.field
        )
    if cfr.count() > 1:
        logger.warning(f"service_now: More than 1 rate was found "
                       f"for field: {cfv.field.name}. The first "
                       f"rate will be selected")
    return cfr.first()


def get_clean_custom_field_values(bpia):
    # Go through custom fields for an object and remove all password fields
    custom_fields = {}
    cfvs = bpia.get_cfv_manager().all()
    for cfv in cfvs:
        if cfv.field.type != 'PWD':
            custom_fields[cfv.field.label] = cfv.value
    preconfigs = bpia.preconfiguration_values.all()
    for preconfig in preconfigs:
        for pre_cfv in preconfig.get_cfv_manager().all():
            if pre_cfv.field.type != 'PWD':
                custom_fields[pre_cfv.field.label] = pre_cfv.value
    return custom_fields


def get_sysid_for_users(snowitsm, base_url, order):
    # Requested By
    requested_by = order.owner.user
    sysid_for_req_by = sysid_username_then_email(requested_by, base_url,
                                                 snowitsm, order)
    # Requested For
    sysid_for_req_for = sysid_for_req_by
    if order.recipient:
        recipient = order.recipient.user
        sysid_for_req_for = sysid_username_then_email(recipient, base_url,
                                                      snowitsm, order)
    return sysid_for_req_by, sysid_for_req_for


def sysid_username_then_email(user, base_url, snowitsm, order):
    """
    Try to get the sysid for the CloudBolt user first by username, if that
    doesn't work try email.
    """
    user_name = user.username
    try:
        sysid = get_snow_user_sys_id(user_name, base_url, snowitsm, order)
    except Exception as e:
        if str(e).find('Unable to find data matching order owner') == 0:
            # If user can't be found using username, try email
            user_name = user.email
            sysid = get_snow_user_sys_id(user_name, base_url, snowitsm, order)
        else:
            raise
    return sysid


def get_snow_user_sys_id(user_name, base_url, snowitsm, order):
    snow_user_data = lookup_ci(table_name='sys_user',
                               ci_query={'user_name': user_name},
                               base_url=base_url,
                               return_ci=['sys_id'],
                               conn=snowitsm)
    if not snow_user_data:
        err = 'Unable to find data matching order owner in ServiceNow. '
        err += f'requested_by: {user_name} --> order: {order.id}'
        logger.warning(err)
        raise Exception(err)

    sysid = snow_user_data['sys_id']
    if not sysid:
        err = f"ServiceNow sys_id for '{user_name}' not found"
        raise Exception(err)

    return sysid


def create_service_request(wrapper, base_url, order, req_by, req_for,
                           oi):
    request_url = f"{base_url}/api/now/table/sc_request"
    description, rate = create_description_and_rate(order, oi)
    data = {"requested_by": req_by,
            "requested_for": req_for,
            "description": description,
            "price": str(rate),
            }

    logger.info(f'service_now: SNOW PAYLOAD: {data}')
    headers = {'Accept': 'application/json',
               'Content-Type': 'application/json'}

    json_data = json.dumps(data)

    try:
        raw_response = wrapper.service_now_request(method="POST",
                                                   url=request_url,
                                                   headers=headers,
                                                   body=json_data)
        response = raw_response.json()
        sys_id = response["result"]["sys_id"]
        request_number = response["result"]["number"]
        # logger.debug(f'RESPONSE: {response}')
        if not sys_id:
            raise NameError("ServiceNow sys_id not found")
        else:
            if type(oi) == BlueprintOrderItem:
                add_servicenow_info_to_oi(oi, request_number, sys_id)
            elif type(oi) == list:
                for smoi in oi:
                    add_servicenow_info_to_oi(smoi, request_number, sys_id)
        # Check approved and auto-approve in CB if already approved
        approval_status = response['result']['approval']
        return ("", "", ""), approval_status
    except Exception as e:
        return ("FAILURE", f"Exception: {e}", ""), ""


def add_servicenow_info_to_oi(oi,request_number, sys_id):
    from orders.views import add_cfvs_to_order_item
    add_cfvs_to_order_item(
        oi, {"snow_order_request_number": request_number}
    )
    add_cfvs_to_order_item(
        oi, {"snow_order_submit_sys_id": sys_id}
    )


def get_approver():
    approver, created = User.objects.get_or_create(
        email="service-now@noreply.com",
        defaults={"username": "service-now@noreply.com",
                  "first_name": "Service",
                  "last_name": "Now"})
    return approver


def approve_order(order):
    approver = get_approver()
    profile = approver.userprofile
    order.approved_by = profile
    order.approved_date = get_current_time()
    order.status = 'ACTIVE'
    order.save()

    logger.debug('Before order event saved')
    history_msg = f"The '{order}' has been approved through ServiceNow by: {profile.user.get_full_name()}"
    order.add_event("APPROVED", history_msg, profile=profile)
    logger.debug('After order event saved')

    parent_job = None

    # Saving job objects will cause them be kicked off by the
    # job engine within a minute
    jobs = []
    order_items = [oi.cast() for oi in order.orderitem_set.filter()]
    for order_item in order_items:
        jobtype = getattr(order_item, "job_type", None)
        if not jobtype:
            # the job type will default to the first word of the class type
            # ex. "provision", "decom"

            jobtype = str(order_item.real_type).split(" ", 1)[0]
        quantity = 1

        # quantity is a special field on order_items.  If an
        # order_item has the quantity field, kick off that many
        # jobs
        if (
                hasattr(order_item, "quantity")
                and order_item.quantity is not None
                and order_item.quantity != ""
        ):
            quantity = int(order_item.quantity)
        for i in range(quantity):
            job = Job(
                job_parameters=order_item,
                type=jobtype,
                owner=order.owner,
                parent_job=parent_job,
            )
            job.save()

            # Associate the job with any server(s)
            # This may seem unnecessary because it's done when most jobs
            # run, but it's needed at the very least for scheduled server
            # modification jobs (for changing resources) so they show up on
            # the server as scheduled before they actually run

            # Since ActionJobOrderItem can contain just a resource and not
            # a server, we need to have extra logic here
            if isinstance(order_item, ActionJobOrderItem):
                if order_item.server:
                    servers = [order_item.server]
            else:
                servers = []
                if hasattr(order_item, "server"):
                    servers = [order_item.server]
                elif hasattr(order_item, "servers"):
                    servers = order_item.servers.all()
                for server in servers:
                    server.jobs.add(job)

            jobs.append(job)

    # If it didn't make any jobs, just call it done
    if not jobs:
        order.complete("SUCCESS")

    msg = 'order complete'
    set_progress(f"&nbsp;&nbsp;&nbsp;&nbsp;Order approved: {order.id}")
    return msg
