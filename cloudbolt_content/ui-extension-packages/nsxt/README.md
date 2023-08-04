# NSX-T XUI README

## Overview
This XUI extends the capabilties of NSX-T to the cloudbolt CMP.

This XUI contains a xui_settings.py file that defines common features that can be easily referenced. Part of this file includes the NSXTXUIAPIWrapper class that overwrites and extends the out of the box CloudBolt NSX integration. 

## NSX-T Tagging Features: 
- Creation of two new parameters, defined the settings file.
- XUI is self-contained and can run along-side the NSX-V XUI.
- This XUI will add a tab onto servers that are part of resource handlers.
- This XUI will also add a tab to any resource handler that is defined as part of a Network Virtualization object.
- A parameter will be added to any environments that are NSXT capable, this will allow a tag to be selected at order time.
- The Server tab will check if the server is a member of a resource handler that has NSX-T enabled.  If it is, it will check if it is part of the NSX-T manager inventory.  If it is not, it will display as much.  If it is, it will display tags available and allow adding/removing them. 

## NSX-T Blueprints
This XUI will also create several Blueprints for Self-Service of NSX-T components, each of these components will constrain the management of these resources to their CloudBolt Group/Tenant.  These Blueprints will be created at the time of the XUI installation. These Blueprints will be created with the following names:: 
- NSX-T Infrastructure Group
- NSX-T Distributed Firewall Policy
  - NSX-T Distributed Firewall Rule (Child Blueprint)
- NSX-T Network Segment

### Blueprint Pre-Requisites
- Create a Resource Handler that is connected to an NSX-T instance
- Create a Network Virtualization Platform to connect to NSX-T. Docs:
    https://docs.cloudbolt.io/articles/#!cloudbolt-latest-docs/managing-network-virtualization
- Ensure that you have at least a single Environment configured with an NSX
  Transport Zone, and a Tier 1 Router

## Blueprint Import Behavior
Blueprint import behavior for the XUI can be controlled by changing the properties in the `/var/opt/cloudbolt/proserv/xui/xui_versions.json` file. The XUI only runs initial configuration once per new version. This is controlled by matching the `current_version` number in the config file with the `__version__` number in the `__init__.py` file. If you need configuration to re-run, just reset the `current_version` number in the config file to a lower number than the `__version__` number in the `__init__.py` file, then `systemctl restart httpd`

By Default, this XUI will not Overwrite existing blueprints. If you wish to overwrite existing blueprints, you can set `OVERWRITE_EXISTING_BLUEPRINTS` to `true` in the `xui_settings.py` file.

When a Blueprint is using a remote source, the actions are only updated at initial creation. Setting `SET_ACTIONS_TO_REMOTE_SOURCE` to `true` in the config file would set each action to use the remote source - forcing update of the actions when the XUI gets updated. 

### Config file example

    {
        "nsxt": {
            "current_version": "1.0", 
            "SET_ACTIONS_TO_REMOTE_SOURCE": true, 
            "OVERWRITE_EXISTING_BLUEPRINTS": true
        }
    }
