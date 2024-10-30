"""
This script is used to set the password for the local admin user on a Windows
server. It will wait for the server to be powered on, VMTools to be ready, and
for the server to have an IPv4 address. It will then set the password for the
local admin user on the server, update the server password to the new password,
and check to be sure the password was set inside the guest. This script is
intended to be used as an Orchestration Action in CloudBolt.
"""
import time

from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, server=None, *args, **kwargs):
    """
    1. Wait for Server to be available and IPv4 address to be assigned
    2. Set local admin password from the cb_new_password parameter via script
    3. Update the server password to the new password
    4. Check to be sure the password was set inside the guest - the initial
      script will fail retrieving the status of the job because the user
      password changed.
    :param job:
    :param server:
    :param args:
    :param kwargs:
    :return:
    """
    wait_for_ipv4(server)
    set_admin_password(server)
    server.password = server.cb_new_password
    server.save()
    check_password_working(server)
    return 'SUCCESS', "", ""


def set_admin_password(server):
    """
    Set the password for the local admin user on the server
    :param server: Server object
    :return:
    """
    script = get_change_password_script(server.username, server.cb_new_password)
    try:
        server.execute_script(script_contents=script)
    except Exception as e:
        logger.debug(f"Error communicating to guest after password was set: {e}"
                     f". This is expected")
    return


def check_password_working(server):
    """
    Check to be sure the password was set successfully by running a script
    :param server: Server object
    :return:
    """
    try:
        set_progress("Checking if password was set successfully")
        server.execute_script(script_contents="echo 'Password updated'")
    except Exception as e:
        logger.error(f"Error setting password: {e}")
        raise Exception("Error setting password")


def get_change_password_script(username, new_password):
    """
    Generate the script to change the password
    :param username:
    :param new_password:
    :return:
    """
    return f"""echo "{username}:{new_password}" | chpasswd"""


def wait_for_ipv4(server, max_sleep=600, sleep_time=10):
    """
    Wait for the VM to be powered on, VMTools to be ready, and for the server
    to have an IPv4 address
    :param server:
    :return:
    """
    total_sleep = 0
    while total_sleep < max_sleep:
        vc_vm = get_vm_by_instance_uuid(server)
        power = vc_vm.summary.runtime.powerState
        if power != "poweredOn" or vc_vm.guest.toolsStatus != "toolsOk":
            set_progress(f"Waiting for server {server.hostname} to be powered "
                         f"on and VMTools to be ready. Total sleep_time: "
                         f"{sleep_time}")
            time.sleep(sleep_time)
            total_sleep += sleep_time
        else:
            break

    while total_sleep < max_sleep:
        server.refresh_info()
        if server.ip:
            if server.ip == server.ipv4_address:
                set_progress(f"IPv4 address {server.ipv4_address} found on "
                             f"server {server.hostname}")
                return
        set_progress(f"Waiting for IPv4 address on server {server.hostname}")
        time.sleep(sleep_time)
        total_sleep += sleep_time


def get_vm_by_instance_uuid(server):
    """
    Get the VM object from pyvmomi by the instance_uuid
    :param server:
    :return:
    """
    rh = server.resource_handler.cast()
    wrapper = rh.get_api_wrapper()
    si = wrapper.si_connection
    content = si.content
    search_index = content.searchIndex
    instance_uuid = server.vmwareserverinfo.instance_uuid
    vm = search_index.FindByUuid(None, instance_uuid, True, True)
    return vm