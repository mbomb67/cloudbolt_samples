#!/usr/bin/env python

"""
Handles updating a user based on data returned from a Single Sign-On (SSO)
Identity Provider (IdP) as part of the login process for SSO. This hook can
be updated to handle any extra data you track in your SSO and map it to the
appropriate values for a given User.
"""

from typing import Any, Optional

from django.utils.translation import gettext as _

from accounts.models import User, Group, Role, GroupRoleMembership
from authentication.sso.models import BaseSSOProvider
from authentication.sso.services import SSOInterface
from jobs.models import Job
from utilities.logger import ThreadLogger
from utilities.models import LDAPUtility

logger = ThreadLogger(__name__)

LDAP_DOMAIN_SSO_ATTR_NAME = "ldapDomain"

"""
GROUPS_MAP is a dictionary that maps the groups from the SSO IdP to groups in 
CloudBolt and the roles that should be assigned to the user for each group.
The key is the group from the SSO IdP and the value is a dictionary with the
following:
- group: the name of the group in CloudBolt
- roles: a list of roles that should be assigned to the user for the group
If a group from the SSO IdP is not in the GROUPS_MAP, the script will attempt
to add the group to the user's groups in CloudBolt if a group exists in the 
system that matches the group name. If the group does not exist in CloudBolt,
the script will log a warning and skip the group. 
Using the GROUPS_MAP is optional. 
"""
GROUPS_MAP = {
    "example_group": {
        "group": "Example Group",
        "roles": ["requestor"],
    },
}

# Default roles to assign to a user if no roles are provided by the SSO IdP
DEFAULT_ROLES = ["requestor"]


def run(
    job: Optional[Job] = None,
    user: Optional[User] = None,
    attrs_from_sso: Optional[dict] = None,
    sso_instance: Optional[BaseSSOProvider] = None,
    creating: bool = False,
    **kwargs,
):
    """Update the input user based on `attrs_from_sso` from the SSO IdP

    :param job: Not used, but all hooks need to take this an as argument
    :param user: User object
    :param attrs_from_sso: dict, attribute names and values from the SSO IdP
    :param sso_instance: BaseSSOProvider object, or child class
    :param creating: bool, True if the User was just created, False otherwise
    :param kwargs: dict, anything else passed to this hook
    :return: Tuple[str, str, str] Status, Output, Errors
    """
    logger.debug("Running hook {}".format(__name__))
    logger.debug(f"user: {user}")
    logger.debug(f"attrs_from_sso: {attrs_from_sso}")
    logger.debug(f"sso_instance: {sso_instance}")
    logger.debug(f"creating: {creating}")
    # make sure we have a User
    if not user:
        msg = _("No 'user' passed to hook 'SSO User Update.' Exiting...")
        logger.error(msg)
        return "FAILURE", "", msg
    # make sure the User is up-to-date
    user.refresh_from_db()
    # make sure we have data from the SSO
    if not attrs_from_sso:
        msg = _("No 'attrs_from_sso' passed to hook 'SSO User Update.' Exiting...")
        logger.error(msg)
        return "FAILURE", "", msg
    # special handling for the username in case it includes a domain: ie user@domain.com
    username, ldap_domain_name = None, None
    if sso_instance.user_attribute_uid in attrs_from_sso:
        full_username: str = _get_first_item_if_list(
            attrs_from_sso[sso_instance.user_attribute_uid]
        )
        if "@" in full_username:
            username, ldap_domain_name = SSOInterface.split_username_and_domain(
                full_username, sso_provider=sso_instance
            )

    # make the dict of User attributes to update
    # `user_attr_map` will be dict mapping user attribute name to the value received from
    # the SSO IdP for this attribute
    # for example {"first_name": "My New First Name"}
    user_attr_map: dict = SSOInterface.generate_user_attr_map(
        sso_instance, attrs_from_sso
    )
    # if we are handling the username specially and it is in the user_attr_map
    # then make sure to include the value with the domain name removed
    if username and "username" in user_attr_map:
        user_attr_map["username"] = username
    # try to update the User
    updated = _update_obj(user, **user_attr_map)
    # dict and function that can be edited to update a User's profile
    profile_attrs_to_update = {}
    updated |= _update_obj(user.userprofile, **profile_attrs_to_update)

    if not ldap_domain_name:
        ldap_domain_name = _get_ldap_domain_name(sso_instance, attrs_from_sso)
    # try to update the LDAPUtility on the UserProfile of the User
    if ldap_domain_name:
        updated |= _update_ldap_domain(user, ldap_domain_name)
    if updated:
        msg = _("User '{user}' updated.".format(user=user.username))
    else:
        msg = _("No updates required for User '{user}'.".format(user=user.username))

    set_sso_groups(user, attrs_from_sso)
    return "SUCCESS", msg, ""


def set_sso_groups(user, attrs_from_sso):
    """
    Go through the list of groups from the SSO IdP and set the User's groups in
    CloudBolt to match.
    1. Get the list of groups from the SSO IdP
    2. Get the list of groups from CloudBolt
    3. If the group does not exist in CloudBolt, skip
    4. If the group exists in CloudBolt, add it to the User's groups
    """
    # get the list of groups from the SSO IdP
    claim = "http://schemas.microsoft.com/ws/2008/06/identity/claims/groups"
    sso_groups = attrs_from_sso.get(claim, [])
    profile = user.userprofile
    for group in sso_groups:
        # get the group from CloudBolt
        try:
            cb_group_map = GROUPS_MAP[group]
            cb_group_name = cb_group_map["group"]
            try:
                cb_group_roles = cb_group_map["roles"]
            except KeyError:
                cb_group_roles = DEFAULT_ROLES
        except KeyError:
            # If the group is not in the map then log a warning and set the
            # group name as the group from the SSO IdP
            cb_group_name = group
            logger.warning(f"Group mapping for '{group}' not set. Using group "
                           f"name: '{cb_group_name}'")
            cb_group_roles = DEFAULT_ROLES
        try:
            cb_group = Group.objects.get(name=cb_group_name)
        except Group.DoesNotExist:
            # if the group does not exist in CloudBolt, skip it
            logger.warning(f"Group '{group}' does not exist in CloudBolt.")
            continue
        # add the group to the User's groups
        for role in cb_group_roles:
            try:
                cb_role = Role.objects.get(name=role)
            except Role.DoesNotExist:
                logger.warning(f"Role '{role}' does not exist in CloudBolt.")
                continue
            logger.info(f"Adding group '{cb_group}' with role '{cb_role}' to "
                        f"'{profile.username}'")
            grm, _ = GroupRoleMembership.objects.get_or_create(
                group=cb_group, role=cb_role, profile=profile
            )
            profile.grouprolemembership_set.add(grm)
    return


def _get_first_item_if_list(value: Any) -> Any:
    """Returns `value` or the first item of `value` if `value is a list"""
    if isinstance(value, list) and len(value) > 0:
        return value[0]
    return value


def _update_obj(obj: Any, **kwargs) -> bool:
    """Update the various attributes of a an object using kwargs and return a bool

    :param obj: object to update
    :param kwargs: key-value pairs of attributes to update on try to update User
    :return: bool, True if object successfully updated, False otherwise
    """
    updated: bool = False
    for attr_name, new_value in kwargs.items():
        # |= operator is a logical OR equals
        # equivalent to `update = updated or _attr_update(...)`
        # once it is True, it stays True
        updated |= _attr_update(obj, attr_name, new_value)
    if updated:
        # if the object was updated and has a save method, then save it
        try:
            obj.save()
        except (AttributeError, TypeError):
            pass
    return updated


def _attr_update(obj_to_update: Any, attr_name: str, new_value: Any) -> bool:
    """Try to update the given attribute on the given object and return a bool

    :param obj_to_update: the object to update
    :param attr_name: str, the name of the attribute to update
    :param new_value: the new value for the attribute
    :return: bool, True if attribute was updated, False otherwise
    """
    try:
        current_value = getattr(obj_to_update, attr_name)
    except AttributeError as e:
        msg = _(
            f"Cannot update attr '{attr_name}' on '{obj_to_update}' because the attribute does not exist."
        )
        logger.exception(msg)
        raise AttributeError(msg) from e

    is_changed: bool = (current_value != new_value) and (bool(new_value))
    if is_changed:
        setattr(obj_to_update, attr_name, new_value)
    # invert the return b/c it is more intuitive for True to mean that the user WAS updated
    return is_changed


def _get_ldap_domain_name(
    sso_instance: BaseSSOProvider,
    attrs_from_sso: dict,
    ldap_domain_attr_name: Optional[str] = None,
) -> Optional[str]:
    # set default name for LDAP domain attribute, if necessary
    if not ldap_domain_attr_name:
        ldap_domain_attr_name = LDAP_DOMAIN_SSO_ATTR_NAME
    # get the LDAP domain from the attribute
    ldap_domain_name = _get_first_item_if_list(
        attrs_from_sso.get(ldap_domain_attr_name, None)
    )
    # if attrs_from_sso doesn't have the ldap domain name attribute
    if not ldap_domain_name:
        # and we can't get the domain name from the username, then return False
        sso_username_attr = sso_instance.user_attribute_uid
        full_username: str = attrs_from_sso.get(sso_username_attr)
        if full_username:
            __, ldap_domain_name = SSOInterface.split_username_and_domain(
                full_username, sso_provider=sso_instance
            )
    return ldap_domain_name


def _update_ldap_domain(
    user: User,
    ldap_domain_name: str,
) -> bool:
    """Update UserProfile with LDAPUtility, if matching LDAPUtility is found

    :return: bool, True if UserProfile update with new LDAPUtility, False otherwise
    """
    try:
        ldap_utility = LDAPUtility.objects.get(ldap_domain=ldap_domain_name)
    except LDAPUtility.DoesNotExist:
        # log an error and return if we don't find a matching LDAPUtility
        msg = _("LDAPUtility for domain '{domain_name}' does not exist")
        logger.error(msg.format(domain_name=ldap_domain_name))
        return False
    # update the user's UserProfile with the LDAP and save
    updated: bool = _attr_update(user.userprofile, "ldap", ldap_utility)
    if updated:
        user.userprofile.save()
    return updated
