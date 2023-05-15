from common.methods import set_progress
from utilities.logger import ThreadLogger
from servicecatalog.models import ProvisionServerServiceItem
from orders.models import BlueprintOrderItem

logger = ThreadLogger(__name__)

def run(order=None, job=None, logger=None, *args, **kwargs):
    """
    Execute a CloudBolt approval action for a BlueprintOrderItem. If the memory
    or CPU requested for all servers in the request is less than the
    maximum specified by max_cpu or max_mem_gb, auto-approve the request.
    """
    max_mem_gb = {{max_mem_gb}}
    max_cpu = {{max_cpu}}
    cpu_exceeded = False
    mem_exceeded = False
    servers_found = False
    oi = order.orderitem_set.first()
    if type(oi.cast()) is not BlueprintOrderItem:
        set_progress("This plugin is only applicable for BlueprintOrderItems")
        return "SUCCESS", "", ""

    bpoi = oi.blueprintorderitem
    bpia_set = bpoi.blueprintitemarguments_set.all()
    for bpia in bpia_set:
        service_item = bpia.service_item.cast()
        if type(service_item) is not ProvisionServerServiceItem:
            # We only want to auto-approve Server elements
            continue
        servers_found = True
        cfvs = bpia.get_cf_values_as_dict()
        pcvs = bpia.preconfiguration_values.all()
        # First check preconfigurations for CPU and mem values
        for pcv in pcvs:
            pcv_cfvs = pcv.get_cf_values_as_dict()
            if 'cpu_cnt' in pcv_cfvs:
                cpu_cnt = pcv_cfvs["cpu_cnt"]
            if 'mem_size' in pcv_cfvs:
                mem_gb = pcv_cfvs["mem_size"]

        # Then check to see if cpu and mem exist, if not, check params
        try:
            cpu_cnt
        except NameError:
            try:
                cpu_cnt = cfvs["cpu_cnt"]
            except KeyError:
                err_str = 'CPU count cannot be determined for the order.'
                set_progress(err_str)
                raise Exception(err_str)

        try:
            mem_gb
        except NameError:
            try:
                mem_gb = cfvs["mem_size"]
            except KeyError:
                err_str = 'Memory Size cannot be determined for the order.'
                set_progress(err_str)
                raise Exception(err_str)
        # Check cpu_cnt
        if cpu_cnt >= max_cpu:
            set_progress(f'CPU limit for auto-approval exceeded for element: '
                         f'{service_item.name}. cpu_cnt: {cpu_cnt} is >= '
                         f'max_cpu: {max_cpu}')
            cpu_exceeded = True
        # Check mem_gb
        if mem_gb >= max_mem_gb:
            set_progress(f'Memory limit for auto-approval exceeded for element'
                         f': {service_item.name}. mem_gb: {mem_gb} is >= '
                         f'max_mem_gb: {max_mem_gb}')
            mem_exceeded = True

    if servers_found and not cpu_exceeded and not mem_exceeded:
        set_progress(f'Order Auto-Approved. Resources less than defined '
                     f'thresholds')
        order.approve()
    elif not servers_found:
        set_progress(f'No servers requested with order, auto-approval does not'
                     f'apply.')
    else:
        set_progress(f'Resources requested exceed auto-approval levels.')
    return "SUCCESS", "", ""
