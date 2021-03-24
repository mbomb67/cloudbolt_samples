from common.methods import set_progress
from infrastructure.models import CustomField
from infrastructure.models import Environment
from xui.gitlab.api_wrapper import GitLabConnector
from utilities.models import ConnectionInfo
import json
import time
import settings
from ast import literal_eval

"""
Build service item action for Azure Resource Manager Templates Calling GitLab XUI

Note: This is currently structured to only support JSON formatted 
ARM Templates

Prerequisites: 
- gitlab XUI installed in Cloudbolt
- ConnectionInfo Configured for gitlab

"""
def get_value_from_form_data(form_data=None, form_prefix=None, field_name=None):
    field_key = None
    field_value = None
    if "form_prefix" in form_data and not form_prefix:
        form_prefix = form_data.get("form_prefix", None)
    if not form_prefix:
        # Nothing can be found
        return field_key, field_value
    for key_name in form_data:
        if form_prefix + "-" + field_name + "_a" in key_name:
            field_key = key_name
            field_value = (
                form_data[key_name]
                if isinstance(form_data[key_name], str)
                else form_data[key_name][0]
            )
            break
    return field_key, field_value

def generate_options_for_env_id(server=None, **kwargs):
    envs = Environment.objects.filter(
        resource_handler__resource_technology__name="Azure"
    ).values("id", "name")
    options = [(env['id'], env['name']) for env in envs]
    return options

def generate_options_for_resource_group(control_value='', **kwargs):
    if not control_value:
        options = [('---', 'First, Select an Environment')]
        return options

    options = [('---', 'Select a Resource Group')]
    env = Environment.objects.get(id=control_value)
    cb_version = settings.VERSION_INFO["VERSION"]

    if is_version_new_enough(current_version=cb_version, expected_version="9.3"):
        groups = env.custom_field_options.filter(field__name='resource_group_arm')
        if groups:
            # return values somewhat unorthadox, but I need the resource_group_id and the environment id so i can unpack both values
            for g in groups:
                options.append(('{' + '"rg_name": "{}", "env_id": "{}"'.format(g.str_value, env.id) + "}", g.str_value))
            return options
    else:
        groups = env.armresourcegroup_set.all()
        if groups:
            # return values somewhat unorthadox, but I need the resource_group_id and the environment id so i can unpack both values
            for g in groups:
                options.append(('{' + '"rg_name": "{}", "env_id": "{}"'.format(g.name, env.id) + "}", g.name))
            return options

    # this shouldnt happen now that the Environemnts are bounded by the list of ones that have the resource_group_arm or resourcegroup_set data
    return [('---', 'No Resource Groups found in this Environment')]
    
def is_version_new_enough(current_version=None, expected_version=None):
    curr_ver_num = get_version_as_num(current_version)
    exp_ver_num = get_version_as_num(expected_version)
    if curr_ver_num >= exp_ver_num:
        set_progress(f'{curr_ver_num} >= {exp_ver_num}')
        return True
    else:
        set_progress(f'{curr_ver_num} < {exp_ver_num}')
        return False       

def get_version_as_num(cb_version):
    # for each version_portion in a string (9.2.1.whatever.2),
    #   multiply the first number by 256 and the second number by 1
    #   then add those 2 fields to get a version number that should tell me if version 9.3.whatever >= 9.2.whatever
    #   because a similar function doesn't exist in older CB versions (but does in 9.3.1)

    x = 256
    ver_as_num = 0
    for ver in cb_version.split(".")[:2]:
        x = int(x)
        set_progress(f'ver: {ver} -- {x} == {x * ver}')
        ver_as_num = int(ver) * x + ver_as_num
        x = int(x / 256)

    return ver_as_num

def generate_options_for_gitlab_name(server=None, **kwargs):
    cis = ConnectionInfo.objects.filter(
        labels__name='gitlab').values("id", "name")
    options = [(ci['name'], ci['name']) for ci in cis]
    return options

def generate_options_for_project_id(server=None, **kwargs):
    options = [
        ("25216144","cloud_formation")
    ]
    return options

def generate_options_for_params_path(server=None, **kwargs):
    options = [
        ("arm_templates/parameters.json","Default Parameters"),
    ]
    return options

def generate_options_for_template_path(server=None, **kwargs):
    options = [
        ("arm_templates/template.json","Default Template"),
        ("arm_templates/basic_template.json","Basic Template"),
    ]
    return options

def generate_options_for_git_branch(server=None, **kwargs):
    options = [
        ("master","master")
    ]
    return options

def get_or_create_cfs():
    CustomField.objects.get_or_create(
        name='azure_rh_id', type='STR',
        defaults={'label': 'Azure RH ID', 'description': 'Used by the Azure blueprints'}
    )
    CustomField.objects.get_or_create(
        name='azure_region', type='STR',
        defaults={'label': 'Azure Region', 'description': 'Used by the Azure blueprints', 'show_as_attribute': True}
    )
    CustomField.objects.get_or_create(
        name='azure_deployment_name', type='STR',
        defaults={'label': 'ARM Deployment Name', 'description': 'Used by the ARM Template blueprint', 'show_as_attribute': True}
    )
    CustomField.objects.get_or_create(
        name='azure_deployment_id', type='STR',
        defaults={'label': 'ARM Deployment ID', 'description': 'Used by the ARM Template blueprint'}
    )
    CustomField.objects.get_or_create(
        name='azure_correlation_id', type='STR',
        defaults={'label': 'ARM Correlation ID', 'description': 'Used by the ARM Template blueprint'}
    )
    

def run(job, logger=None, **kwargs):
    #GitLab Params
    gitlab_name = '{{ gitlab_name }}'
    project_id = '{{ project_id }}'
    params_path = '{{ params_path }}'
    template_path = '{{ template_path }}'
    git_branch = '{{ git_branch }}'

    #ARM Params
    deployment_name = "{{ name }}"
    resource_group = literal_eval("{{ resource_group }}")['rg_name']
    env_id = '{{ env_id }}'
    timeout = '{{ timeout }}'
    manual_parameters = {
        "virtualMachineName": '{{ virtual_machine_name }}',
        "adminPassword": '{{ admin_password }}'
    }
    resource = kwargs.get('resource')

    #Get parameters file from gitlab
    parameters = {}    
    if params_path != '':
        with GitLabConnector(gitlab_name) as gitlab:
            file_params = gitlab.get_raw_file_as_json(project_id,params_path,git_branch)
        params_dict = json.loads(file_params)["parameters"]
        for key in params_dict.keys():
            parameters[key] = params_dict[key]["value"]
    
    #Add in manual parameters - doing this last allows manual params to 
    #override params set in the params file
    for key in manual_parameters.keys():
        parameters[key] = manual_parameters[key]

    #Get the template from GitLab
    with GitLabConnector(gitlab_name) as gitlab:
        template_string = gitlab.get_raw_file_as_json(project_id,
                                                template_path,git_branch)
    template = json.loads(template_string)

    #Submit the template request
    env = Environment.objects.get(id=env_id)
    handler = env.resource_handler.cast()
    if timeout:
        timeout = int(timeout)
    else:
        timeout = 3600
    wrapper = handler.get_api_wrapper()
    deployment = wrapper.deploy_template(deployment_name, resource_group, 
                                        template, parameters, timeout=300)
    set_progress(f'deployment: {deployment}')
    get_or_create_cfs()
    deploy_props = deployment.properties
    set_progress(f'deployment properties: {deploy_props}')
    resource.azure_rh_id = handler.id
    resource.azure_region = env.node_location
    resource.azure_deployment_name = deployment_name
    resource.azure_deployment_id = deployment.id
    resource.azure_correlation_id = deploy_props.correlation_id
    i = 0
    for output_resource in deploy_props.additional_properties["outputResources"]:
        field_name_id = f'output_resource_{i}_id'
        CustomField.objects.get_or_create(
            name=field_name_id, type='STR',
            defaults={
                'label': f'ARM Created Resource ID {i}', 
                'description': 'Used by the ARM Template blueprint',
                'show_as_attribute': True
                }
        )
        field_name_type = f'output_resource_{i}_type'
        CustomField.objects.get_or_create(
            name=field_name_type, type='STR',
            defaults={
                'label': f'ARM Created Resource Type {i}', 
                'description': 'Used by the ARM Template blueprint',
                'show_as_attribute': True
            }
        )
        id_value = output_resource["id"]
        type_value = id_value.split('/')[-2]
        resource.set_value_for_custom_field(field_name_id,id_value)
        resource.set_value_for_custom_field(field_name_type,type_value)
        i += 1
    resource.save()
    
    return "", "", ""


if __name__ == '__main__':
    from utilities.logger import ThreadLogger
    logger = ThreadLogger(__name__)
    print(run(None, logger))