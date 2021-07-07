# CloudBolt ARM Builder Blueprint

##Important Note: 
ARM Templates can be destructive if not properly configured. You will want to undergo extensive testing with each ARM template prior to deploying in a production environment. The actions that handle destroy for the created resources will delete any resources that are tied to a deployment, which with the way that Azure ARM templates work could be resources that are shared by other resources (Storage Accounts is one example). You will want to structure your ARM Templates so that all deployed resources are unique to the deployment.

## Function
This is a library of python actions that enable a Blueprint that automates the creation of a CloudBolt Blueprint that submits a request for an ARM Template.

1. Allows you to upload an ARM template which will be stored on the CloudBolt appliance.
2. Create a Blueprint that leverages that ARM template to create Azure Resources
3. Also allows for the upload of a parameters file. From the upload of a parameters file, create each parameter on the blueprint with the correct type.

## Configuration
1. Copy all files in this directory to your CloudBolt appliance. The files can live wherever you would like under */var/opt/cloudbolt/proserv*, but we recommend placing them under */var/opt/cloudbolt/proserv/arm_builder*
2. If you selected a directory other than recommended, you will need to change the *ROOT_DIR* variable in the *build.py* file to reflect the new location. 
3. Create a new blueprint, name the blueprint *ARM Builder*, do not assign it a resource type. 
4. Under the build tab of the new blueprint:
    * Select *+ Add* to add an Action Blueprint Item
    * Select *CloudBolt Plug-In*
    * Select *+ Create a new plug-in action now*
    * Name the new plug-in action *ARM Builder*
    * Under the file location selection for the plug-in action select the *Fetch from URL* radio button
    * Enter *file:///var/opt/cloudbolt/proserv/arm_builder/build.py* as the location for the file. If you selected a different file path you will want to modify this to reflect the chosen file path. 
    * Save the plug-in
5. The ARM Builder Blueprint should now be ready to submit.

### After a blueprint has been created by the ARM Builder Blueprint
1. Check the Parameters set on the blueprint. Any single valued parameters will automatically pass that value in to the ARM template, the user will not be able to select the value. Make sure that these values are what you want to pass.
2. Check all parameters with dropdown options. All parameters with allowedValues have had those values created as dropdown options. You can delete options that you do not want the user to select from. 
3. Where applicable use regex on your parameters to catch things that aren't in line with Azure's requirements. 
4. The order that the parameters are displayed in the blueprint request form can be modified by going to *Admin > Parameter Display Sequence*
5. Any parameters that were  brought in with a location of [resourceGroup().location] can be either deleted from the blueprint or just ignored. 

### Other Notes 
* This blueprint should be selectively exposed, you do not want every CloudBolt group to have access, but rather a subset of users who understand what they are doing with ARM Templates. 
* To get started with ARM Templates Azure has a github repo with lots of samples: https://github.com/Azure/azure-quickstart-templates

### Optional Scripts
The *return_environment_options.py* and *return_value_of_controlling_field.py* scripts in this directory can also optionally be used to help with parameter selection. To use them, you will need to create a new action for generating options and then point to these scripts to use them. More information surrounding their use can be found in the scripts themselves. 