"""
Apply the relevant Ansible Automation Configurations to the Server being provisioned,
based on the values for any Ansible Automation Configuration type Parameters.

This is an Overwrite of the OOB plugin to allow for sensitive extra vars to not
be passed, preventing the overwrite of Job Template surveys
"""

from connectors import connector_for
from jobs.progress_wrapper import ProgressWrapper
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, **kwargs):
    # There should only ever be 1 Server per Provision Job that this is running against
    server = job.server_set.first()
    # Note we unfortunately can't use something like values_list with CFV's "value"
    aap_apps = [
        cfv.value for cfv in server.custom_field_values.filter(field__type="AAP")
    ]
    aap_apps = set(aap_apps)
    config_mgr = connector_for(server.environment, "install_application")
    applicable_aap_apps = [a for a in aap_apps if a.aap == config_mgr]

    result = "SUCCESS"
    if config_mgr and applicable_aap_apps:
        result, aap_job_ids = config_mgr.apply_configurations_in_aap(
            server, applicable_aap_apps, job
        )
        if result == "SUCCESS" and aap_job_ids:
            return config_mgr.wait_for_completion_in_aap(job, aap_job_ids)

    return result, "", ""


def apply_configurations_in_aap(config_mgr, server, aap_applications,
                                job) -> tuple:
    """
    Does the actual work of taking a defined AAPApplication (combo of Group/Inventory and
    Job Template/Workflow Job Template) and applying it to a Server, via the AAPService
    """
    from connectors.ansible_automation_platform.models import AAPHost
    from connectors.ansible_automation_platform.service import AAPService

    job_progress = ProgressWrapper(job, logger=logger)
    extra_vars, protected_keys = config_mgr.generate_extra_vars(server)

    # Overwrite any protected keys - this should prevent CloudBolt from passing
    # secrets to Ansible
    protected_keys = []

    # Loop through the applications and add the server to the appropriate
    # inventory or group and launch the job template or workflow.
    aap_job_ids = []
    for aap_application in aap_applications:
        aap_service = AAPService.from_conf_id(aap_application.aap.id)
        # Who likes capital E Exception?
        # The AnsibleTowerWrapper throws them, so we have to catch them.
        # The exceptions are all going to be classes from requests so in the future
        # we should refactor the wrapper to be more precise about what it throws.
        # noinspection PyBroadException
        try:
            job_progress.add_message(f"{server.hostname} - {server.ip}")
            response_json = aap_service.add_host_to_aap(
                server.hostname, server.ip, aap_application
            )
            host_id = response_json.get("id")
            inventory = config_mgr.inventories.get(
                id=response_json.get("inventory"))
            AAPHost.objects.update_or_create(
                id=host_id,
                inventory=inventory,
                server=server,
                defaults={"json": response_json},
            )
            job_progress.add_message(
                f"Added host {server.hostname} to Ansible Automation Platform {aap_application.aap.name}."
            )
            job_progress.add_message(f"Remote Host ID: {host_id}")
        except Exception as e:
            # This particular error just means that we've already done this step for either the same
            # AAPApplication or another with the same Inventory/Group. We can safely ignore it, which
            # will allow people to re-run Ansible Automation Configurations on the same Server or select
            # multiple of them without having to worry if their Inventory/Group overlap
            if (
                    str(e)
                    != 'Ansible Tower returned code 400 : {"__all__":["Host with this Name and Inventory already exists."]}'
            ):
                job_progress.add_message(
                    f"Failed to add host {server.hostname} to {aap_application.aap.name}. Error: {e}"
                )
                return "FAILURE", aap_job_ids

        try:
            aap_job_id = aap_service.launch_application_job_against_host(
                server.hostname,
                aap_application,
                extra_vars=extra_vars,
                protected_keys=protected_keys,
            )
            job_progress.add_message(
                f"Launched job Ansible Job {aap_job_id} for host {server.hostname} in {aap_application.aap.name}."
            )
            aap_job_ids.append(aap_job_id)
        # noinspection PyBroadException
        except Exception as e:
            job_progress.add_message(
                f"Failed to launch job against host {server.hostname} in {aap_application.aap.name}. Error: {e}"
            )
            return "FAILURE", aap_job_ids

    return "SUCCESS", aap_job_ids