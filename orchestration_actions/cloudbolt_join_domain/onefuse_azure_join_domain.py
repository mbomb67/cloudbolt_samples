#!/usr/local/bin/python

from __future__ import print_function
import sys
import re
from common.methods import set_progress
from utilities.logger import ThreadLogger
import json

logger = ThreadLogger(__name__)

"""
Example Hook to join a Windows server to a particular OU of an AD domain when
OneFuse has pre-created the computer object in AD. This sample leverages the 
Azure run utility to execute in guest scripts

Requires 2 parameters:

    1) domain_username - The username used to add the server to the domain. Ex:
    LAB\Administrator

    2) domain_password - The password used to add the server to the domain.

Additional parameters: In order to run the necessary script on the server,
CloudBolt must know the username and password with which to log into the
template. By default, CloudBolt uses the Administrator username. The password
can be set using either the 'Windows Server Password' or 'VMware Template Password'
parameters. If the username on the template is not Administrator, use the
'Server Username' parameter.

This hook will be especially useful as a post-provisioning hook.

Note: If the server is already in the specified domain (even if it is not in the
requested OU), the script will not change anything and will print a message
saying the server cannot be added to the domain again.

Note: The password provided will be temporarily stored in plain text in a PowerShell script
file on the system, but the file will be deleted when the task is complete.

Testing this hook: To speed up the development cycle of testing this hook, it
can be run from the command line. Using a completed server provision job, run
`./join_domain_ou.py <job id>`. The job's originating order must have had the
above parameters already set.
"""


def azure_run(server, script):
    # Setup Azure Compute Client
    rh = server.environment.resource_handler.cast()
    client = rh.get_api_wrapper().compute_client

    # Get Azure VM_Name and Resource Group
    vm_name = server.get_vm_name()

    azure_server_info = rh.tech_specific_server_details(server)
    resource_group_name = vm_name
    if azure_server_info and azure_server_info.resource_group:
        resource_group_name = azure_server_info.resource_group

    if server.is_windows():
        command_id = 'RunPowerShellScript'
    else:
        command_id = 'RunShellScript'

    run_command_parameters = {
        'command_id': command_id,
        'script': script.splitlines()
    }
    try:
        poller = client.virtual_machines.run_command(
            resource_group_name,
            vm_name,
            run_command_parameters
        )
        result = poller.result()  # Blocking till executed
        for value in result.value:
            server.stream_output(True, value.message)
            logger.debug(f'Azure Run output: {value.message}')

        if "succeeded" in result.value[0].code:
            return "SUCCESS", result.value[0].display_status, ""
    except Exception as e:
        return "FAILURE", e.message, e.message
    return "FAILURE", "", ""


def get_domain_from_dn(dn):
    domain = ''
    for dc in dn.split(','):
        if dc[0:3] == 'DC=':
            if domain:
                domain = f'{domain}.'
            domain = f'{domain}{dc[3:]}'
    return domain


def run(job, logger, **kwargs):
    server = job.server_set.first()
    if server:
        set_progress(f"OneFuse Join AD Domain running for server {server}")
        logger.debug(f"Dictionary of keyword args passed to this "
                     f"plug-in: {kwargs.items()}")
    else:
        logger.info("Server object not found, exiting.")
        return "", "", ""

    try:
        onefuse_ad_str = server.OneFuse_AD
        onefuse_ad = json.loads(onefuse_ad_str)
    except:
        set_progress('OneFuse_AD not defined for server, exiting script')
        return "", "", ""
    dn = onefuse_ad["finalOu"]
    domain = get_domain_from_dn(dn)

    # If the job this is associated with fails, don't do anything
    if job.status == "FAILURE":
        return "", "", ""

    if not server.is_windows():
        msg = "Skipping joining domain for non-windows VM"
        return "", msg, ""
    if not server.resource_handler.cast().can_run_scripts_on_servers:
        logger.info("Skipping hook, cannot run scripts on guest")
        return "", "", ""

    username = server.get_value_for_custom_field("domain_username")
    password = server.get_value_for_custom_field("domain_password")

    fail_msg = "Parameter '{}' not set, cannot run hook"

    # confirm that all custom fields have been set
    if not domain:
        msg = fail_msg.format("domain")
        return "FAILURE", msg, ""
    if not username:
        msg = fail_msg.format("domain_username")
        return "FAILURE", msg, ""
    elif not password:
        msg = fail_msg.format("domain_password")
        return "FAILURE", msg, ""

    script = [
        "Add-Computer ",
        '-DomainName "{}" '.format(domain.strip()),
        "-Credential ",
        "(",
        "New-Object ",
        "System.Management.Automation.PSCredential (",
        '"{}", '.format(username.strip()),
        '(ConvertTo-SecureString "{}" -AsPlainText -Force)'.format(
            password.strip()),
        ")) ",
    ]
    script = "".join(script)
    set_progress(f'Joining Domain: {domain}. Azure run command takes time...')

    try:
        azure_run_response = azure_run(server, script)
        return azure_run_response
    except RuntimeError as err:
        set_progress(str(err))
        # If the error is due to already being in the domain, it's OK
        errmsg = re.sub(r"\r", "", str(err))
        errmsg = re.sub(r"\n", "", errmsg)
        found = re.search(r"because it is already in that domain", errmsg)
        if not found:
            return "FAILURE", str(err), ""


if __name__ == "__main__":
    from jobs.models import Job
    logger = ThreadLogger(__name__)
    job_id = sys.argv[1]
    job = Job.objects.get(id=job_id)

    print(run(job, logger))


