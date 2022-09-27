import time

from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


# Global variables control how long to wait for tools and power statuses to
# change
MAX_SLEEP = 120
SLEEP_TIME = 5


def run(job, server=None, **kwargs):
    wait_for_vm_tools(server)
    return "SUCCESS", "", ""

def get_vm_tools_status(server):
    vm = get_vm(server)
    tools_status = vm.summary.guest.toolsRunningStatus
    return tools_status


def get_vm(server):
    rh = server.resource_handler
    vc_rh = rh.cast()
    wrapper = vc_rh.get_api_wrapper()
    si = wrapper._get_connection()
    search_index = si.content.searchIndex
    uuid = server.resource_handler_svr_id
    vm = search_index.FindByUuid(None, uuid, True)
    return vm


def wait_for_vm_tools(server):
    tools_status = None
    total_sleep = 0
    while tools_status != 'guestToolsRunning':
        tools_status = get_vm_tools_status(server)
        logger.info(f'Waiting for VM Tools. tools_status: {tools_status}. '
                    f'sleeping for {SLEEP_TIME} seconds')
        time.sleep(SLEEP_TIME)
        total_sleep = total_sleep + SLEEP_TIME
        if total_sleep > MAX_SLEEP:
            raise Exception("Sleep time exceeded waiting for VM Tools.")