"""
This actions should be used as a recurring job to periodically query for any
Approvals waiting on approval from ServiceNow. Once ServiceNow has approved the
Request, this action will set the order to Approved in CloudBolt.
"""

import requests
import cbhooks
from itsm.servicenow.models.servicenow_itsm import ServiceNowITSM
from django.utils.http import urlencode
from common.methods import set_progress
from orders.models import Order, get_current_time, ActionJobOrderItem, \
    ServerModOrderItem
from django.contrib.auth.models import User
from utilities.exceptions import CloudBoltException
from django.utils.html import escape, format_html
from jobs.models import Job
from django.utils.translation import ugettext as _

"""
    ServiceNow Service Request Queue
    ~~~~~~~~~~~~~~~~~~~~~~~~~
    This one was designed to work with the ITSM SNOW integration

    Created By: Steven Manross (CloudBolt)
    Modified by: Mike Bombard
"""


def run(job=None, logger=None, **kwargs):
    set_progress('Running ServiceNow Request queue manager')
    pending_bpoi_orders = get_pending_bpoi_orders()
    pending_smoi_orders = get_pending_smoi_orders()
    if not pending_bpoi_orders and not pending_smoi_orders:
        msg = "There were no pending orders waiting on ServiceNow approvals"
        return "SUCCESS", msg, ""

    snowitsm = ServiceNowITSM.objects.first()  # there's likely only one
    wrapper = snowitsm.get_api_wrapper()
    base_url = wrapper.service_now_instance_url.replace("/login.do", "")

    """
    Loop through pending orders which have a PENDING status.
    Update status to be the status which has been set in ServiceNow
    """
    check_bpoi_order_approval(pending_bpoi_orders, base_url, snowitsm)
    check_smoi_order_approval(pending_smoi_orders, base_url, snowitsm)
    return "SUCCESS", "", ""


def get_pending_bpoi_orders():
    # Get all PENDING orders that have ServiceNow sys_ids atached
    return Order.objects.filter(
        status='PENDING',
        orderitem__blueprintorderitem__isnull=False,
        orderitem__blueprintorderitem__custom_field_values__field__name='snow_order_submit_sys_id'
    )


def get_pending_smoi_orders():
    temp_smoi_orders = Order.objects.filter(
        status='PENDING',
        orderitem__servermodorderitem__isnull=False,
        orderitem__servermodorderitem__custom_field_values__field__name='snow_order_submit_sys_id'
    )
    # Some orders can have multiplke ServerModOrderItems, so loop through all
    # found Orders and only return a single order per group of SMOIs
    pending_smoi_orders = []
    for smoi_order in temp_smoi_orders:
        if smoi_order not in pending_smoi_orders:
            pending_smoi_orders.append(smoi_order)
    return pending_smoi_orders


def check_bpoi_order_approval(pending_bpoi_orders, base_url, snowitsm):
    for order in pending_bpoi_orders:
        set_progress(f'SNOW pending order: {order.id} -> {order.status}')
        bpoi = order.orderitem_set.filter(
            blueprintorderitem__isnull=False,
            blueprintorderitem__custom_field_values__field__name='snow_order_submit_sys_id'
        ).first().cast()
        ci_sys_id = bpoi.custom_field_values.filter(
            field__name='snow_order_submit_sys_id').first().value
        set_progress(
            f'&nbsp;&nbsp;&nbsp;&nbsp;Getting approval status for order # '
            f'({order.id}) for SNOW request_id: {ci_sys_id}')
        approval_status = get_approval_status(ci_sys_id, base_url, snowitsm)
        approve_order_from_status(order, approval_status)


def check_smoi_order_approval(pending_smoi_orders, base_url, snowitsm):
    for order in pending_smoi_orders:
        set_progress(f'SNOW pending order: {order.id} -> {order.status}')
        smois = order.orderitem_set.filter(
            servermodorderitem__isnull=False,
            servermodorderitem__custom_field_values__field__name='snow_order_submit_sys_id'
        )
        smoi = smois.first().cast()
        ci_sys_id = smoi.custom_field_values.filter(
            field__name='snow_order_submit_sys_id').first().value
        set_progress(
            f'&nbsp;&nbsp;&nbsp;&nbsp;Getting approval status for order # '
            f'({order.id}) for SNOW request_id: {ci_sys_id}')
        approval_status = get_approval_status(ci_sys_id, base_url,
                                              snowitsm)
        approve_order_from_status(order, approval_status)



def get_approval_status(ci_sys_id, base_url, snowitsm):
    approval_data = lookup_ci(table_name='sc_request',
                              ci_name='sys_id',
                              ci_value=ci_sys_id,
                              base_url=base_url,
                              return_ci=['number', 'stage', 'approval'],
                              conn=snowitsm)
    # set_progress(f'JSON Data: {approval_data}')
    approval_status = approval_data["approval"]
    set_progress(f'&nbsp;&nbsp;&nbsp;&nbsp;SNOW approval state: '
                 f'{approval_status}')
    return approval_status


def approve_order_from_status(order, approval_status):
    if approval_status == 'approved':
        approve_order(order)
    elif approval_status == 'rejected':
        reject_order(order)
    elif approval_status == 'cancelled':
        cancel_order(order)
    else:
        set_progress(
            f"&nbsp;&nbsp;&nbsp;&nbsp;Order was not approved in "
            f"ServiceNow:Order ID: {order.id} --> state: "
            f"{approval_status}")


def reject_order(order):
    approver = get_approver()
    profile = approver.userprofile
    order.approved_by = profile
    order.approved_date = get_current_time()
    order.denied_reason = 'Order denied in ServiceNow'
    order.status = 'DENIED'
    order.save()


def cancel_order(order):
    approver = get_approver()
    profile = approver.userprofile
    order.approved_by = profile
    order.approved_date = get_current_time()
    order.denied_reason = 'Order Cancelled in ServiceNow'
    order.status = 'CANCELED'
    order.save()


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

    history_msg = f"The '{order}' has been approved through ServiceNow by: {profile.user.get_full_name()}"
    order.add_event("APPROVED", history_msg, profile=profile)

    try:
        cbhooks.run_hooks("pre_order_execution", order=order)
    except cbhooks.exceptions.HookFailureException as e:
        order.status = "FAILURE"
        order.save()
        msg = _(
            f"Failed to run hook for order approval. Status: {e.status},"
            f" Output: {e.output}, Errors: {e.errors}"
        )

        history_msg = _(f"The '{escape(order)}' order has failed.")

        order.add_event("FAILED", history_msg, profile=profile)
        raise CloudBoltException(msg)

    if order.status == "PENDING" and order.approvers.exists():
        link = format_html(
            f"<a href='{order.get_absolute_url()}'>{order}</a>"
        )
        msg = format_html(
            _(
                f"{link} was approved, and then marked as 'Pending' by a 'Post-Order Approval' Orchestration Action."
            ),
        )
        return [], msg

    parent_job = None

    # Saving job objects will cause them to be kicked off by the
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
    return jobs, msg


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
    # set_progress(f'response = {response.text}')

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
