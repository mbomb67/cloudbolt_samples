"""
Build service item action for Azure Resource Manager Templates deployment

This action was created by the ARM Builder Blueprint

Do not edit this script directly as all resources provisioned by the ARM
Builder Blueprint use this script. If you need to make one-off modifications,
copy this script and create a new action leveraged by the blueprint that needs
the modifications.
"""
from jobs.models import Job
from utilities.events import add_server_event

if __name__ == "__main__":
    import os
    import sys
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
    sys.path.append("/opt/cloudbolt")
    sys.path.append("/var/opt/cloudbolt/proserv")
    django.setup()

from common.methods import set_progress
from infrastructure.models import CustomField, Server
from infrastructure.models import Environment
import json
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def create_cf(cf_name, cf_label, description, cf_type="STR", required=False,
              **kwargs):
    defaults = {
        'label': cf_label,
        'description': description,
        'required': required,
    }
    for key, value in kwargs.items():
        defaults[key] = value

    cf = CustomField.objects.get_or_create(
        name=cf_name,
        type=cf_type,
        defaults=defaults
    )


def get_or_create_cfs():
    create_cf('azure_rh_id', 'Azure RH ID', 'Used by the Azure blueprints')
    create_cf('azure_region', 'Azure Region', 'Used by the Azure blueprints',
              show_on_servers=True)
    create_cf('azure_deployment_id', 'ARM Deployment ID',
              'Used by the ARM Template blueprint', show_on_servers=True)
    create_cf('azure_correlation_id', 'ARM Correlation ID',
              'Used by the ARM Template blueprint')


def get_location_from_environment(env):
    return env.node_location


def get_provider_type_from_id(resource_id):
    return resource_id.split('/')[6]


def get_resource_type_from_id(resource_id):
    return resource_id.split('/')[7]


def get_api_version_key_from_id(id_value):
    provider_type = get_provider_type_from_id(id_value)
    resource_type = get_resource_type_from_id(id_value)
    ms_type = f'{provider_type}/{resource_type}'
    id_split = id_value.split('/')
    if len(id_split) == 11:
        ms_type = f'{ms_type}/{id_split[9]}'
    ms_type_us = ms_type.replace('.', '').replace('/', '_').lower()
    api_version_key = f'{ms_type_us}_api_version'
    return api_version_key


def get_azure_server_name(id_value):
    return id_value.split('/')[8]


def create_field_set_value(field_name_id, id_value, i, resource):
    create_cf(field_name_id, f'ARM Created Resource {i} ID',
              'Used by the ARM Template blueprint',
              show_on_servers=True)
    resource.set_value_for_custom_field(field_name_id, id_value)
    return resource


def run(job, **kwargs):
    resource = kwargs.get('resource')
    if resource:
        set_progress(f'Starting deploy of ARM Template for resource: '
                     f'{resource}')
        params_file_path = resource.get_cfv_for_custom_field(
            'arm_builder_params_file_path').value
        template_file_path = resource.get_cfv_for_custom_field(
            'arm_builder_template_file_path').value
        env_id = resource.get_cfv_for_custom_field(
            'arm_builder_env_id').value
        resource_group = resource.get_cfv_for_custom_field(
            'arm_builder_resource_group').value
        deployment_name = resource.get_cfv_for_custom_field(
            'arm_builder_deployment_name').value
        if not (params_file_path and template_file_path and env_id and
                resource_group):
            msg = (f'Required parameter not found. params_file_path: '
                   f'{params_file_path}, template_file_path: '
                   f'{template_file_path}, env_id: {env_id}, resource_group: '
                   f'{resource_group}')
            set_progress(msg)
            return "FAILURE", msg, ""

        # Generic ARM Params
        timeout = 900

        # Other Params
        owner = job.owner
        group = resource.group
        env = Environment.objects.get(id=env_id)
        rh = env.resource_handler.cast()

        # Get parameters from file
        parameters = {}
        json_content = json.loads(open(params_file_path, "r").read())
        params_dict = json_content["parameters"]
        for key in params_dict.keys():
            parameters[key] = params_dict[key]["value"]


        # Override params set in the params file by params included set on the
        # Resource
        cfvs = resource.get_cf_values_as_dict()
        bp_id = resource.blueprint_id
        arm_prefix = f'arm_builder_{bp_id}_'
        for key in cfvs.keys():
            if key.find(arm_prefix) == 0:
                param_key = key.replace(arm_prefix, '')
                value = cfvs[key]
                if value == '[resourceGroup().location]':
                    value = get_location_from_environment(env)
                parameters[param_key] = value
                cf = CustomField.objects.get(name=key)
                if cf.type == 'PWD':
                    logger.debug(f'Setting password: {param_key} to: ******')
                else:
                    logger.debug(f'Setting param: {param_key} to: {value}')

        # Get the template from file path
        template = json.loads(open(template_file_path, "r").read())

        # Write the API Versions back to the Resource - to be used on delete
        for template_resource in template["resources"]:
            api_version = template_resource["apiVersion"]
            ms_type = template_resource["type"]
            type_split = ms_type.split('/')
            ms_type_us = ms_type.replace('.', '').replace('/', '_').lower()
            api_version_key = f'{ms_type_us}_api_version'
            # If the API version is already set for an Azure type on the
            # resource, no need to set again, check to see if it exists then
            # set if not present
            try:
                val = resource.get_cfv_for_custom_field(api_version_key).value
                logger.debug(f'Value already set for api_version_key: '
                             f'{api_version_key}, value: {val}')
            except AttributeError:
                api_key_name = f'{type_split[-1]} API Version'
                logger.debug(f'Creating CF: {api_version_key}')
                create_cf(api_version_key, api_key_name, (
                    f'The API Version that {ms_type} '
                    f'resources were provisioned with in this deployment')
                )
                resource.set_value_for_custom_field(api_version_key,
                                                    api_version)
            except NameError:
                resource.set_value_for_custom_field(api_version_key,
                                                    api_version)

        # Submit the template request
        if timeout:
            timeout = int(timeout)
        else:
            timeout = 3600
        wrapper = rh.get_api_wrapper()
        logger.debug(f'Submitting request for ARM template. deployment_name: '
                     f'{deployment_name}, resource_group: {resource_group}, '
                     f'template: {template}')
        set_progress(f'Submitting ARM request to Azure. This can take a while.'
                     f' Timeout is set to: {timeout}')
        deployment = wrapper.deploy_template(deployment_name, resource_group,
                                             template, parameters,
                                             timeout=timeout)
        set_progress(f'Deployment created successfully')
        logger.debug(f'deployment info: {deployment}')
        get_or_create_cfs()
        deploy_props = deployment.properties
        logger.debug(f'deployment properties: {deploy_props}')
        resource.azure_rh_id = rh.id
        resource.azure_region = env.node_location
        resource.azure_deployment_id = deployment.id
        resource.azure_correlation_id = deploy_props.correlation_id
        resource.resource_group = resource_group
        i = 0
        for output_resource in deploy_props.additional_properties[
            "outputResources"]:
            id_value = output_resource["id"]
            type_value = id_value.split('/')[-2]

            # If a server, create the CloudBolt Server object
            if type_value == 'virtualMachines':
                resource_client = wrapper.resource_client
                api_version_key = get_api_version_key_from_id(id_value)
                api_version = resource.get_cfv_for_custom_field(
                    api_version_key).value
                vm = resource_client.resources.get_by_id(id_value,
                                                         api_version)
                vm_dict = vm.__dict__
                svr_id = vm_dict["properties"]["vmId"]
                location = vm_dict["location"]
                node_size = vm_dict["properties"]["hardwareProfile"]["vmSize"]
                disk_ids = [vm_dict["properties"]["storageProfile"]["osDisk"]
                            ["managedDisk"]["id"]]
                for disk in vm_dict["properties"]["storageProfile"]\
                        ["dataDisks"]:
                    disk_ids.append(disk["managedDisk"]["id"])
                if svr_id:
                    # Server manager does not have the create_or_update method,
                    # so we do this manually.
                    try:
                        server = Server.objects.get(
                            resource_handler_svr_id=svr_id)
                        server.resource = resource
                        server.group = group
                        server.owner = resource.owner
                        server.environment = env
                        server.save()
                        logger.info(
                            f"Found existing server record: '{server}'")
                    except Server.DoesNotExist:
                        logger.info(
                            f"Creating new server with resource_handler_svr_id "
                            f"'{svr_id}', resource '{resource}', group '{group}', "
                            f"owner '{resource.owner}', and "
                            f"environment '{env}'"
                        )
                        server_name = get_azure_server_name(id_value)
                        server = Server(
                            hostname=server_name,
                            resource_handler_svr_id=svr_id,
                            resource=resource,
                            group=group,
                            owner=resource.owner,
                            environment=env,
                            resource_handler=rh
                        )
                        server.save()
                        server.resource_group = resource_group
                        server.save()

                        tech_dict = {
                            "location": location,
                            "resource_group": resource_group,
                            "storage_account": None,
                            "extensions": [],
                            "availability_set": None,
                            "node_size": node_size,
                        }
                        rh.update_tech_specific_server_details(server,
                                                               tech_dict, None)
                        server.refresh_info()
                    # Add server to the job.server_set, and set creation event
                    job.server_set.add(server)
                    job.save()
                    msg = "Server created by ARM Template job"
                    add_server_event("CREATION", server, msg,
                                     profile=job.owner, job=job)
                    api_version_key = get_api_version_key_from_id(disk_ids[0])
                    api_key_name = f"{api_version_key.split('_')[1]} API Version"
                    create_cf(api_version_key, api_key_name, (
                        f'The API Version that Microsoft.Compute/disks '
                        f'resources were provisioned with in this deployment')
                              )
                    # Write the api_version for Virtual Machine to the disk
                    # value
                    resource.set_value_for_custom_field(api_version_key,
                                                        '2021-04-01')
                    for disk_id in disk_ids:
                        field_name_id = f'output_resource_{i}_id'
                        resource = create_field_set_value(field_name_id,
                                                          disk_id, i, resource)
                        i += 1
                        resource.save()
            field_name_id = f'output_resource_{i}_id'
            resource = create_field_set_value(field_name_id, id_value, i,
                                              resource)

            i += 1
            resource.save()

        return "SUCCESS", "ARM Template deployment complete", ""
    else:
        msg = f'Resource not found.'
        set_progress(msg)
        return "FAILURE", msg, ""


if __name__ == "__main__":
    job_id = sys.argv[1]
    j = Job.objects.get(id=job_id)
    run = run(j)
    if run[0] == "FAILURE":
        set_progress(run[1])
