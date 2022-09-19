"""
Renames a vSphere Virtual Machine Guest to match the Server name in CloudBolt
"""

import time

from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# Global variables control how long to wait for tools and power statuses to
# change
MAX_SLEEP = 120
SLEEP_TIME = 5


def run(job, server=None, **kwargs):
    # If the job this is associated with fails, don't do anything
    if job.status == "FAILURE":
        return "", "", ""
    server.power_on()
    logger.info(f"Powering On Server")
    wait_for_vm_tools(server)
    if not server.is_windows():
        msg = "Skipping rename computer for non-windows VM"
        return "", msg, ""
    if not server.resource_handler.cast().can_run_scripts_on_servers:
        logger.info("Skipping hook, cannot run scripts on guest")
        return "", "", ""

    set_progress("Renaming Computer based on parameters")
    hostname = server.hostname

    # confirm that all custom fields have been set
    if not hostname:
        msg = f"Parameter 'hostname' not set, cannot run hook"
        return "FAILURE", msg, ""

    script = f'Rename-Computer -NewName {hostname} -Force'

    # For debugging
    username = server.get_credentials()["username"]
    msg = f"Executing script on server using username '{username}. Script: " \
          f"{script}'"
    logger.info(msg)

    try:
        output = server.execute_script(script_contents=script)
        logger.info(f"Script returned output: {output}")
        server.power_off()
        logger.info(f"Powering Off Server")
        wait_for_power_off(server)
        server.power_on()
        logger.info(f"Powering On Server")
        wait_for_vm_tools(server)
    except RuntimeError as err:
        set_progress(str(err))
        return "FAILURE", str(err), ""

    return "", "", ""


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


def wait_for_power_off(server):
    power_state = None
    total_sleep = 0
    while power_state != 'poweredOff':
        power_state = get_vm_power_state(server)
        logger.info(f'Waiting for VM to power off. power_state: {power_state}.'
                    f' sleeping for {SLEEP_TIME} seconds')
        time.sleep(SLEEP_TIME)
        total_sleep = total_sleep + SLEEP_TIME
        if total_sleep > MAX_SLEEP:
            raise Exception("Sleep time exceeded waiting for VM Tools.")


def get_vm_power_state(server):
    vm = get_vm(server)
    power_state = vm.summary.runtime.powerState
    return power_state
