"""
CloudBolt Plugin that will apply AAP Configurations to all servers provisioned
before this step via Terraform.

Either the required_ansible_automation_configurations or the
additional_ansible_automation_configurations parameters should be set as
Blueprint parameters to know which AAP configurations to apply.

This is useful when provisioning a server with Terraform and then applying AAP.

Pre-Requisites:
1. An Ansible Automation Configuration Manager must be configured in CloudBolt.
2. The AAP configurations must be defined in the Config Manager.
3. The AAP Config Manager must be associated with the all Environments where
    the AAP configurations will be applied.
4. The AAP configurations must be associated with the Blueprint as a Custom
    Field Value.
"""
from common.methods import set_progress
from orders.models import BlueprintOrderItem
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, *args, **kwargs):
    logger.debug(f"kwargs: {kwargs}")
    bpoi = BlueprintOrderItem.objects.get(id=kwargs.get("blueprint_order_item"))
    logger.debug(f"Blueprint Order Item: {bpoi}")
    blueprint_cfvs = bpoi.get_cf_values_as_dict()
    logger.debug(f"Blueprint Custom Field Values: {blueprint_cfvs}")
    servers = get_servers_from_tf_ops(bpoi)
    logger.debug(f"Servers: {servers}")
    for server in servers:
        # Apply CFS to the server
        for cf_name, cf_value in blueprint_cfvs.items():
            logger.debug(f"Setting server custom field: {cf_name} to "
                         f"{cf_value}")
            server.set_value_for_custom_field(cf_name, cf_value)
        set_progress(f"Applying AAP configurations to server: {server}")
        invoke_ansible_applications(job, server)


def invoke_ansible_applications(job, server):
    from connectors import connector_for
    aap_apps = [
        cfv.value for cfv in
        server.custom_field_values.filter(field__type="AAP")
    ]
    aap_apps = set(aap_apps)
    config_mgr = connector_for(server.environment, "install_application")
    applicable_aap_apps = [a for a in aap_apps if a.aap == config_mgr]

    if config_mgr and applicable_aap_apps:
        logger.info(f"Applying AAP configurations to server: {server}, "
                    f"AAPs: {applicable_aap_apps}, Job: {job}, config_mgr: "
                    f"{config_mgr}")
        result, aap_job_ids = config_mgr.apply_configurations_in_aap(
            server, applicable_aap_apps, job
        )
        if result == "SUCCESS" and aap_job_ids:
            return config_mgr.wait_for_completion_in_aap(job, aap_job_ids)


def get_servers_from_tf_ops(bpoi):
    servers = []
    order = bpoi.order
    tf_order_items = order.orderitem_set.filter(
        genericjoborderitem__isnull=False
    )
    for tfoi in tf_order_items:
        tf_job = tfoi.cast().job_set.first()
        state_resource = tf_job.resource_set.first()
        for s in state_resource.server_set.all():
            servers.append(s)
    return servers
