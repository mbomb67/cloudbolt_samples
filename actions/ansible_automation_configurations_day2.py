"""
Apply the selected Ansible Automation Configurations to the Server(s),
with Action Input options potentially based on Ansible Automation Configuration type Parameters.
"""

from accounts.models import Group
from cbhooks.hookmodules.generate_options_for_ansible_automation import (
    get_options_list as get_cf_options,
)
from infrastructure.models import CustomField
from orders.models import CustomFieldValue
from utilities.models import GlobalPreferences
from jobs.progress_wrapper import ProgressWrapper
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_ansible_automation_configurations(**kwargs):
    env = None
    server = kwargs.get("server")
    if server:
        # We can only limit by the Env/Group if we're considering a single Server
        env = server.environment
        group = server.group
        gp = GlobalPreferences.get()

        # This logic is a simplified version of get_cfs_for_context
        env_cf_ids = env.custom_fields.filter(type="AAP").values_list("id", flat=True)
        grp_cf_ids = group.custom_fields.filter(type="AAP").values_list("id", flat=True)

        if gp.inherit_group_parameters and group.parent:
            inherited_cfs = group.get_inherited_custom_fields
            # add inherited custom fields
            for cf_name, group_id in inherited_cfs.items():
                parent = Group.objects.get(id=group_id)
                grp_cf_ids |= parent.custom_fields.filter(type="AAP").values_list(
                    "id", flat=True
                )

        cf_ids = list(env_cf_ids) + list(grp_cf_ids)
        aap_cfs = CustomField.objects.filter(id__in=cf_ids)
        aap_apps = set()
        for cf in aap_cfs:
            from orders.views import get_optional_values_for_field

            options = get_optional_values_for_field(
                cf, environment=env, group=group
            ).get("options", [])
            for option in options:
                if isinstance(option, CustomFieldValue):
                    aap_app = option.value
                    aap_apps.add((aap_app.id, f"{aap_app.aap.name}: {aap_app.name}"))
                else:
                    # If it's a tuple instead of a CFV it should already be in the right format
                    aap_apps.add(option)
        if aap_apps:
            return list(aap_apps)

    # If we're lacking AAP CFs and/or options, fall back to the same thing we do
    # when generating options for AAP CFs.
    kwargs["environment"] = env
    # Note that the method signature requires passing a CF but it isn't used so if it
    # isn't passed in we just want to make sure there's something
    kwargs["field"] = kwargs.get("field", None) or CustomField.objects.first()
    return get_cf_options(**kwargs)


def run(job, **kwargs):
    # Need to be here for unit tests
    from common.methods import set_progress
    from connectors import connector_for
    from connectors.ansible_automation_platform.models import AAPApplication

    servers = kwargs.get("servers", [])
    # We need to use a list of IDs, not just a string, but it has to be a
    # string to start to avoid syntax errors, so turn it into a list here
    aap_app_ids = "{{ansible_automation_configurations}}"
    ids_list = aap_app_ids.strip("][").split(", ")
    ids_list = [aap_id.strip("'") for aap_id in ids_list]
    aap_apps = [AAPApplication.objects.get(id=app_id) for app_id in ids_list]

    overall_result = "SUCCESS"
    for server in servers:
        config_mgr = connector_for(server.environment, "install_application")
        applicable_aap_apps = [a for a in aap_apps if a.aap == config_mgr]

        if config_mgr and applicable_aap_apps:
            set_progress(
                f"Applying the following Ansible Automation Configurations to {server}: {applicable_aap_apps}"
            )
            initial_result, aap_job_ids = apply_configurations_in_aap(
                config_mgr, server, applicable_aap_apps, job
            )
            if initial_result == "SUCCESS" and aap_job_ids:
                return_tuple = config_mgr.wait_for_completion_in_aap(job, aap_job_ids)
                # The 2nd and 3rd items in the tuple are always "" regardless
                if return_tuple[0] == "FAILURE":
                    overall_result = "FAILURE"
            else:
                overall_result = initial_result
        else:
            # Not very likely when running on a single Server, but more so in bulk
            set_progress(
                f"Skipping {server} because it has no applicable Ansible Automation Configurations"
            )

    return overall_result, "", ""


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
    for key in protected_keys:
        del extra_vars[key]
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