"""
Apply the relevant Ansible Automation Configurations to the specified server
tiers being provisioned, based on the values for any Ansible Automation
Configuration type Parameters.

This is an Overwrite of the OOB plugin to allow for sensitive extra vars to not
be passed, preventing the overwrite of Job Template surveys, and it intended to
be used as a build plugin on a Blueprint.

Note - this plugin is configured to run the configs against
"""
import ast

from common.methods import set_progress
from connectors import connector_for
from connectors.ansible_automation_platform.models import AAPApplication
from infrastructure.views import server_list
from jobs.progress_wrapper import ProgressWrapper
from servicecatalog.models import ServiceBlueprint, ServiceItem
from utilities.exceptions import CloudBoltException
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_ansible_config_ids(field, blueprint, **kwargs):
    logger.debug("generate_options_for_ansible_config_ids")
    logger.debug(f'kwargs: {kwargs}')
    confs = AAPApplication.objects.all()
    return [(c.id, c.name) for c in confs]


def generate_options_for_blueprints_filter(field, blueprint, **kwargs):
    return [(bp.id, bp.name) for bp in ServiceBlueprint.objects.all()]


def run(job, server=None, **kwargs):
    if not server:
        return "FAILURE", "", "Server object was not passed to the plugin. "
    ansible_config_ids = ast.literal_eval("""{{ ansible_config_ids }}""")
    logger.debug(f'ansible_config_ids: {ansible_config_ids}')
    bp_ids = ast.literal_eval("""{{ blueprints_filter }}""")
    bps = ServiceBlueprint.objects.filter(id__in=bp_ids)
    if server.service_item.blueprint not in bps:
        logger.debug(f'Server {server.hostname} was not created from a BP '
                     f'associated with this plugin. Skipping...')
        return "SUCCESS", "", ""

    aap_apps = AAPApplication.objects.filter(id__in=ansible_config_ids)

    result = "SUCCESS"

    # Check server power status and power on if powered off (Ansible cannot run
    # Deprovisioning without server being powered on)
    server.refresh_info()
    if server.power_status is "POWEROFF":
        set_progress(f'Server is not powered on - powering on so Ansible can '
                     f'communicate with the guest.')
        server.power_on()

    # This will loop through and run one app job template against all servers in
    # selected tiers. It will then wait for these job templates to complete
    # before moving on to the next app. This is to prevent race conditions for
    # cases where multiple apps are selected that need to be installed in a
    # specific order.
    for app in aap_apps:
        all_aap_job_ids = []
        env = server.environment
        # All servers in the same tier will have the same config manager for
        # all being in the same Environment
        config_mgr = connector_for(env, "install_application")
        # Checking to be sure the app selected is available to the env
        applicable_aap_apps = [a for a in [app] if a.aap == config_mgr]
        if config_mgr and applicable_aap_apps:
            result, aap_job_ids = apply_configurations_in_aap(
                config_mgr, server, applicable_aap_apps, job
            )
            if result != "SUCCESS":
                raise CloudBoltException(result)
            all_aap_job_ids.extend(aap_job_ids)
        else:
            logger.warning(
                f"Ansible Automation Platform Configuration: {app} is "
                f"not applicable to Environment {env.name}. Skipping."
            )
        if all_aap_job_ids:
            result, _, _ = config_mgr.wait_for_completion_in_aap(
                job, all_aap_job_ids
            )

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
