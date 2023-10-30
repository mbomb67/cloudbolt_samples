"""
CloudBolt Plugin for migrating projects from VRA8 to CloudBolt

Prerequisites:
- An LDAP connection must be configured in CloudBolt named to match the name of
    the vRA tenant in the iDP domain
- Configure a Connection Info object in CloudBolt with "vra8" as a label
- CloudBolt must be at least on version 2023.4.1

Features:
- groups_map: Allows you to pass in a dict mapping vRA Business Group names to
    CloudBolt group names. This is useful if you want to use different names
    in CloudBolt than vRA. This would also allow several vRA Business Groups to
    map to the same CloudBolt group.
    Example: {"vRA Business Group Name": "CloudBolt Group Name"}


"""
import ast
import json
from urllib.parse import urlencode
import requests
from django.contrib.auth.models import User

from vra.vra8_connection import (VRealizeAutomation8Connection,
                                 generate_options_for_vra_connection,
                                 generate_options_for_vra_projects)

from accounts.models import Group, Role
from c2_wrapper import create_custom_field
from common.methods import set_progress
from utilities.models import ConnectionInfo, LDAPUtility, LDAPMapping
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


CONN_INFO_ID = "{{vra_connection}}"
PROJECT_IDS = "{{vra_projects}}"
try:
    PROJECT_IDS = ast.literal_eval(PROJECT_IDS)
except Exception:
    PROJECT_IDS = PROJECT_IDS

# An optional dict mapping vRA Business Group names to CloudBolt group names
# Example: {"vRA Business Group Name": "CloudBolt Group Name"}
GROUPS_MAP = "{{groups_map}}"
if GROUPS_MAP and GROUPS_MAP.find("{{") == -1:
    GROUPS_MAP = json.loads(GROUPS_MAP)
else:
    # A Default GROUP_MAP for your environment could be set here
    GROUPS_MAP = {}
VERIFY_CERTS = False
# Dict mapping a vRA Role to CloudBolt Roles
# Example: {"vRA Role Name": ["CloudBolt Role Name"]}
# Default ROLE_MAP
ROLE_MAP = {
    "administrators": ["group_admin"],
    "members": ["requestor"],
    "viewers": ["viewer"],
    "supervisor": ["approver"],
}

# Dict mapping LDAP domain names in vRA to CloudBolt LDAPUtility names
# Example: {"vRA Domain Name": "CloudBolt LDAPUtility Name"}
# Default DOMAIN_MAP
DOMAIN_MAP = {
    "cbsw.io": "cloudbolt.io",
}


def run(job=None, logger=None, **kwargs):
    set_progress("Starting migration of vRA Projects to CloudBolt")
    vra = VRealizeAutomation8Connection(CONN_INFO_ID)
    for project_id in PROJECT_IDS:
        migrate_project(vra, project_id)
    return "SUCCESS", "", ""


def migrate_project(vra, project_id):
    # Get the project from vRA
    project = vra.get_project(project_id)

    # Create the group in CloudBolt
    defaults = {
        "description": project["description"],
    }
    group_name = project["name"]
    if group_name in GROUPS_MAP:
        group_name = GROUPS_MAP[group_name]
    group, created = Group.objects.get_or_create(name=group_name,
                                                 type_id=1, defaults=defaults)

    # Assign group roles to group members
    assign_group_roles(project, group)

    # Migrate Custom Properties
    migrate_custom_properties(project, group)


def migrate_custom_properties(project, group):
    # Get the custom properties from the project
    custom_properties = project["customProperties"]

    # Create Custom Fields for each custom property
    for custom_property, value in custom_properties.items():
        logger.debug(f"Migrating Custom Property: {custom_property}, with "
                     f"Value: {value}, to Group: {group.name}")
        # Check for encrypted property - this is not supported
        if value.startswith("((secret:"):
            logger.warning(f"Encrypted custom property not supported: "
                           f"{custom_property}. Skipping")
            continue
        # Create the custom field
        field = create_custom_field(
            name=custom_property,
            label=custom_property,
            description="Created by vRA Project Migration",
            type="STR",
            required=True,
            show_as_attribute=False,
            show_on_servers=False,
        )

        # Add the custom field to the group
        group.custom_fields.add(field)

        # Add the custom field to the group's custom field values
        group.custom_field_options.get_or_create(value=value, field=field)



def assign_group_roles(project, group):
    for key, cb_roles in ROLE_MAP.items():
        if key in project:
            for vra_assignment in project[key]:
                assignment_type = vra_assignment["type"]
                vra_user = vra_assignment["email"]
                if assignment_type == "group":
                    # Get LDAPUtility for Domain use last domain (name of
                    # mapping in vRA) to find the LDAP Utility in CloudBolt
                    # In vRA Groups are set as groupname@domain@idpdomain
                    ldap_name = vra_user.split("@")[2]
                    # Group usernames include domain name - used to search DN
                    domain_name = vra_user.split("@")[1]
                    ldap_group_name = vra_user.split("@")[0]
                    try:
                        group_dn, ldap = get_ldap_group_dn(
                            ldap_name, ldap_group_name, domain_name
                        )
                    except Exception as e:
                        logger.warning(f"Error getting LDAP Group DN: {e}")
                        continue
                    # Get or Create new LDAP Group Mapping for each role
                    mapping, _ = LDAPMapping.objects.get_or_create(
                        ldap_group_dn=group_dn, ldap_utility_id=ldap.id)
                    mapping_group, _ = mapping.ldapmappinggroup_set.get_or_create(
                        group=group
                    )
                    for cb_role in cb_roles:
                        # Add Roles to LDAPMappingGroup
                        mapping_group.roles.add(Role.objects.get(name=cb_role))
                elif assignment_type == "user":
                    # User mappings not currently supported
                    logger.warning(f"User mappings not currently supported. "
                                   f"Skipping user: {vra_user}")
                else:
                    raise Exception(f"Unknown assignment type: "
                                    f"{assignment_type}")
                group.grouprolemembership_set.add()


def get_ldap_user_type(sAMAccountType):
    if sAMAccountType == "805306368":
        return "user"
    elif sAMAccountType == "268435456":
        return "group"
    else:
        raise Exception(f"Unknown sAMAccountType: {sAMAccountType}")


def get_ldap_group_dn(ldap_name, group_name, domain_name):
    if ldap_name in DOMAIN_MAP:
        ldap_name = DOMAIN_MAP[ldap_name]
    ldap = LDAPUtility.objects.get(ldap_domain=ldap_name)
    ldap.runUserSearch()
    # Search LDAP for group return the DN
    attrs = ["dn", "sAMAccountName", "sAMAccountType"]
    ldap_response = ldap.runUserSearch(group_name, find=attrs)
    if not ldap_response:
        raise Exception(f"Group not found in LDAP: {group_name} in domain: "
                        f"{domain_name}. Skipping")
    if len(ldap_response) > 1:
        raise Exception(f"Multiple groups found in LDAP for group: {group_name}"
                        f" in domain: {domain_name}. Skipping")
    group_attrs = ldap_response[0][1]
    if get_ldap_user_type(group_attrs["sAMAccountType"][0]) != "group":
        raise Exception(f"LDAP User Type is not group for group: {group_name} "
                        f"in domain: {domain_name}. Skipping")
    ldap_group_dn = ldap_response[0][0]
    return ldap_group_dn, ldap
