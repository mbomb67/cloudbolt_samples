"""

Teardown Service Item Action for ARM Template Blueprint

Note - depending on the Azure service being deleted, you may get errors 
stating that the API version passed isn't correct. You will need to 
Add a valid API version for the provider_type and resource_type in 
to the get_api_version method. The error thrown should include a list
of the valid API versions for the service that is being deleted. 

Also note - this action will delete all resources associated with a Virtual
Machine to include attached disks and networks. 

"""

if __name__ == '__main__':
    import django
    django.setup()
from resourcehandlers.azure_arm.models import AzureARMHandler
from common.methods import set_progress
import sys

def get_provider_type_from_id(resource_id):
    return resource_id.split('/')[6]

def get_resource_type_from_id(resource_id):
    return resource_id.split('/')[7]

def get_api_version(provider_type,resource_type=None):
    if provider_type == 'Microsoft.Compute' and resource_type =='virtualMachines':
        api_version = '2021-03-01'
    elif provider_type == 'Microsoft.Compute' and resource_type =='disks':
        api_version = '2020-12-01'
    else:
        api_version = '2018-05-01'
    return api_version

def run(job, *args, **kwargs):
    resource = job.resource_set.first()
    if resource:
        set_progress(f"ARM Delete plugin running for resource: {resource}")
        try: 
            azure_rh_id = resource.azure_rh_id
            if azure_rh_id == None: 
                raise Exception(f'RH ID not found.')
        except: 
            msg = "No RH ID set on the blueprint, continuing"
            set_progress(msg)
            return "SUCCESS", msg, ""
        try: 
            azure_deployment_id = resource.azure_deployment_id
            if azure_rh_id == None: 
                raise Exception(f'Deployment ID not found.')
        except: 
            msg = "No Deployment ID set on the blueprint, continuing"
            set_progress(msg)
            return "SUCCESS", msg, ""
            
        #Instantiate Azure Resource Client
        rh: AzureARMHandler = AzureARMHandler.objects.get(id=azure_rh_id)
        wrapper = rh.get_api_wrapper()
        resource_client = wrapper.resource_client
        azure_resource_ids = []

        #Gather IDs to be deleted
        result_found = True
        resource_dict = resource.get_cf_values_as_dict()
        i = 0
        while result_found == True:
            field_name_id = f'output_resource_{i}_id'
            field_name_type = f'output_resource_{i}_type'
            try: 
                resource_id = resource_dict[field_name_id]
                resource_type = resource_dict[field_name_type]
            except:
                result_found = False
                break
            if resource_id not in azure_resource_ids:
                azure_resource_ids.append(resource_id)
            #If the resource is a virtual machine ensure that we get all NICs and Storage from the VM: 
            if resource_type == 'virtualMachines':
                print(f'Processing Virtual Machine Dependent Resources. ID: {resource_id}')
                provider_type = get_provider_type_from_id(resource_id)
                resource_type = get_resource_type_from_id(resource_id)
                api_version = get_api_version(provider_type,resource_type)
                try: 
                    vm = resource_client.resources.get_by_id(resource_id,api_version)
                except: 
                    set_progress(f'VM with ID: {resource_id} was not found, continuing.')
                    i += 1
                    continue
                vm_dict = vm.as_dict()
                other_resource_ids = []
                os_disk_id = vm_dict["properties"]["storageProfile"]["osDisk"]["managedDisk"]["id"]
                other_resource_ids.append(os_disk_id)
                for disk in vm_dict["properties"]["storageProfile"]["dataDisks"]:
                    other_resource_ids.append(disk["managedDisk"]["id"])
                for nic in vm_dict["properties"]["networkProfile"]["networkInterfaces"]:
                    other_resource_ids.append(nic["id"])
                for other_resource_id in other_resource_ids:
                    #Some of these resources may have been part of the template execution, prevent duplicate deletes
                    if other_resource_id not in azure_resource_ids:
                        azure_resource_ids.append(other_resource_id)
            i += 1
            
        #Capture Deployment ID to be deleted with teardown
        azure_resource_ids.append(azure_deployment_id)

        #Delete Resources
        for resource_id in azure_resource_ids:
            provider_type = get_provider_type_from_id(resource_id)
            resource_type = get_resource_type_from_id(resource_id)
            set_progress(f'Deleting for provider_type: {provider_type}, resource_type: {resource_type}')
            api_version = get_api_version(provider_type,resource_type)
            set_progress(f'api_version: {api_version}')
            set_progress(f'Deleting Azure Resource with ID: {resource_id}')
            try:
                response = resource_client.resources.delete_by_id(resource_id,api_version)
                #Need to wait for each delete to complete in case there are resources dependent on others (VM disks, etc.)
                wrapper._wait_on_operation(response)
            except: 
                error_string = (f'Error: {sys.exc_info()[0]}. {sys.exc_info()[1]}, '
                                f'line: {sys.exc_info()[2].tb_lineno}')
                set_progress(f'All resource_ids: {azure_resource_ids}')
                set_progress(f'Delete Failed on resource_id: {resource_id}')
                set_progress(error_string)
                raise Exception(f'ARM Delete Failed. {error_string}')
        return "SUCCESS", "All resources successfully deleted", ""

    else: 
        set_progress("Resource was not found")
        return "SUCCESS", "Resource was not found", ""
    
if __name__ == '__main__':
    run()