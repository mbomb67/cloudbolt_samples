from django.template.defaultfilters import pluralize
from django.utils.translation import ugettext as _

from accounts.models import Group, UserProfile
from infrastructure.forms import ServerChangeAttributesValidationHelper
from infrastructure.helper_functions import raise_error_if_not_permitted
from utilities import events
from utilities.models import GlobalPreferences
from infrastructure.models import Server, Environment
from externalcontent.models import OSFamily, OSBuild


def generate_options_for_owner(server=None, servers=None, profile=None, **kwargs):
    if (not server and not servers) or not profile:
        return {"hidden": True}
    user_display_option = (
        GlobalPreferences.get().default_user_display_scheme or "username"
    )
    user_display_option = "user__" + user_display_option

    userprofile_qs = UserProfile.objects_for_profile(profile).filter(
        user__is_active=True
    )
    userprofiles = userprofile_qs.order_by(user_display_option).values_list(
        "id", user_display_option
    )
    ret = {"options": list(userprofiles)}
    if server and server.owner:
        initial = (
            server.owner.id,
            getattr(server.owner, f"{user_display_option}", server.owner.username),
        )
        if initial not in ret["options"]:
            ret["options"].append(initial)
        ret["initial_value"] = initial
    if not server:
        ret["options"].insert(0, (None, "(no change)"))
        ret["initial_value"] = (None, "(no change)")
        ret["required"] = False
    return ret


def generate_options_for_group(server=None, servers=None, profile=None, **kwargs):
    if (not server and not servers) or not profile:
        return {"hidden": True}
    ret = {
        "options": list(Group.objects_for_profile(profile).values_list("id", "name"))
    }
    if server:
        initial = (server.group.id, server.group.name)
        if initial not in ret["options"]:
            ret["options"].append(initial)
        ret["initial_value"] = initial
    else:
        ret["options"].insert(0, (None, "(no change)"))
        ret["initial_value"] = (None, "(no change)")
        ret["required"] = False
    return ret


def generate_options_for_management_groups(
    server=None, servers=None, profile=None, **kwargs
):
    if not profile or not profile.is_cbadmin or (not server and not servers):
        return {"hidden": True}
    ret = {
        "options": list(Group.objects_for_profile(profile).values_list("id", "name"))
    }
    if server and server.management_groups.exists():
        initial = list(server.management_groups.values_list("id", flat=True))
        for group_id in [
            g for g in initial if g not in [opt[0] for opt in ret["options"]]
        ]:
            group = Group.objects.get(id=group_id)
            ret["options"].append((group.id, group.name))
        ret["initial_value"] = initial
    if not server:
        # An actual value of None won't be shown in the multi-select field in the Servers list
        # bulk dialog, so we instead use the special value "None", which can also be filtered out
        # in update_server to ensure the correct behavior
        ret["options"].insert(0, ("None", "(no change)"))
        ret["initial_value"] = ["None"]
        ret["required"] = False
    return ret


def generate_options_for_environment(server=None, servers=None, profile=None, **kwargs):
    if (not server and not servers) or not profile:
        return {"hidden": True}
    envs_for_profile = list(
        Environment.objects_for_profile(profile)
        .order_by("name")
        .values_list("id", "name")
    )
    # The objects_for_profile above doesn't include unconstrained Envs (ones that aren't directly
    # associated with any Groups), but those should be options so we add them here
    unconstrained_envs = Environment.without_unassigned.filter(
        group=None, tenant=profile.tenant
    ).values_list("id", "name")
    envs_for_profile.extend(unconstrained_envs)
    ret = {"options": envs_for_profile}
    if server and server.environment:
        initial = (server.environment.id, server.environment.name)
        if initial not in ret["options"]:
            ret["options"].append(initial)
        ret["initial_value"] = initial
    else:
        ret["options"].insert(0, (None, "(no change)"))
        ret["initial_value"] = (None, "(no change)")
        ret["required"] = False

    return ret


def generate_options_for_os_build(server=None, servers=None, profile=None, **kwargs):
    if (not server and not servers) or not profile:
        return {"hidden": True}

    available_os_builds = list(
        OSBuild.objects_for_profile(profile, permission="USE").values_list("id", "name")
    )

    if server:
        server_os_builds = list(
            server.environment.os_builds.all().values_list("id", "name")
        )
        server_os_builds_available_to_profile = list()

        for server_os_build in server_os_builds:
            if server_os_build in available_os_builds:
                server_os_builds_available_to_profile.append(server_os_build)

        ret = {"options": server_os_builds_available_to_profile}
    else:
        ret = {"options": available_os_builds}
    if not server or not server.os_build:
        ret["options"].insert(0, (None, "(no change)"))
        ret["initial_value"] = (None, "(no change)")
        ret["required"] = False
    else:
        initial = (server.os_build.id, server.os_build.name)
        if initial not in ret["options"]:
            ret["options"].append(initial)
        ret["initial_value"] = initial
    return ret


def generate_options_for_os_family(server=None, servers=None, profile=None, **kwargs):
    if not server and not servers:
        return {"hidden": True}
    # We use the permission "USE" here because otherwise only Admins would be able to
    # see and modify OS Families
    available_os_families = list(
        OSFamily.objects_for_profile(profile, "USE").all().values_list("id", "name")
    )
    ret = {"options": available_os_families}
    if server and server.os_family:
        initial = (server.os_family.id, server.os_family.name)
        if initial not in ret["options"]:
            ret["options"].append(initial)
        ret["initial_value"] = initial
    if not server:
        ret["options"].insert(0, (None, "(no change)"))
        ret["initial_value"] = (None, "(no change)")
        ret["required"] = False
    return ret


def get_current_status_choice(server, status_choices):
    for slug, ui_facing_name in status_choices:
        if server.status == slug:
            return slug, ui_facing_name
    return None, "Unrecognized Status"


def generate_options_for_status(server=None, servers=None, **kwargs):
    if not server and not servers:
        return {"hidden": True}
    status_options = ["ACTIVE", "HISTORICAL"]
    ret = {}

    status_choices = [
        choice for choice in Server.SERVER_STATUS_CHOICES if choice[0] in status_options
    ]
    ret["options"] = status_choices
    if server:
        current_status_choice = get_current_status_choice(server, status_choices)
        if current_status_choice not in ret["options"]:
            ret["options"].append(current_status_choice)
        ret["initial_value"] = current_status_choice

    if not server:
        ret["options"].insert(0, (None, "(no change)"))
        ret["initial_value"] = (None, "(no change)")
        ret["required"] = False
    return ret


def update_server(kwargs, profile, server) -> int:
    field_count = 0
    event_strings = []
    # Only if the user is an Admin do we allow them to optionally set any Group(s) to get
    # additional management permissions for the Server, without becoming its primary Group
    if profile.is_cbadmin:
        management_groups = kwargs.get("management_groups")
        if management_groups not in [None, "", "None", ["None"]]:
            # The case where the value is ["None"] will be when coming from the Servers list bulk dialog
            # if they don't change anything and just leave it as "(no change)", in which case we shouldn't
            # touch anything. Because of the way the multi-select works, if they do choose one or more
            # Groups there then they will be in the list alongside that special value "None", which we
            # should ignore in order to simply set the Server(s) to the Group(s) selected. Note that removing
            # the special "(no change)" option will wipe any Management Groups(s) from all Server(s), but
            # otherwise there's no way from the bulk dialog to remove only a specific Group(s).
            management_groups = [int(g) for g in management_groups if g != "None"]
            if set(management_groups) != set(
                server.management_groups.values_list("id", flat=True)
            ):
                server.management_groups.set(management_groups)
                val = ", ".join(server.management_groups.values_list("name", flat=True))
                event_strings.append(
                    _("management groups changed to {value}").format(value=val)
                )
                field_count += 1

    for fk_attr_name in [
        "owner",
        "group",
        "environment",
        "os_build",
        "os_family",
    ]:
        new_val = kwargs.get(fk_attr_name)
        if new_val:
            new_val = int(new_val)
            if new_val == getattr(server, fk_attr_name + "_id"):
                # no change
                continue
            setattr(server, fk_attr_name + "_id", new_val)
            field_count += 1
            event_strings.append(
                _("{attr_label} changed to {value}").format(
                    attr_label=fk_attr_name, value=getattr(server, fk_attr_name)
                )
            )
    status = kwargs.get("status")
    if status and status != server.status:
        server.status = status
        event_strings.append(_("status changed to {value}").format(value=status))
        field_count += 1
    server.save()

    events.add_server_event(
        "MODIFICATION",
        server,
        "\n".join(event_strings),
        profile=profile,
        notify_cmdb=True,
    )
    # Update tag values in the public clouds to reflect the updated server attribute values.
    rh = server.resource_handler
    if rh and rh.cast().can_manage_tags:
        rh = rh.cast()
        rh.update_tags(server)
    return field_count


def run(*args, request=None, server=None, servers=None, profile=None, **kwargs):
    if server and not servers:
        servers = [server]
    if not profile:
        raise PermissionError(
            "Could not find user profile, which is required to change attributes"
        )
    raise_error_if_not_permitted(profile, "server.change_attributes", servers)

    validation_errors = gather_validation_errors(kwargs)
    if validation_errors:
        return (
            "FAILURE",
            " ".join(validation_errors),
            _("The changes were not saved as the selections were not valid"),
        )

    for server in servers:
        field_count = update_server(kwargs, profile, server)
    if len(servers) == 1:
        return (
            "SUCCESS",
            _("Updated {} field{} on {}").format(
                field_count, pluralize(field_count), servers[0].hostname
            ),
            "",
        )
    else:
        return (
            "SUCCESS",
            _("Updated {} field{} on {} servers").format(
                field_count, pluralize(field_count), len(servers)
            ),
            "",
        )


def gather_validation_errors(kwargs):
    validation_errors = []
    new_env_id = kwargs.get("environment")
    new_group_id = kwargs.get("group")
    new_owner_id = kwargs.get("owner")
    if new_group_id:
        new_group = Group.objects.get(id=new_group_id)
        if new_owner_id:
            is_valid, msg = ServerChangeAttributesValidationHelper.valid_owner_group(
                UserProfile.objects.get(id=new_owner_id), new_group
            )
            if not is_valid:
                validation_errors.append(msg)

        if new_env_id:
            (
                is_valid,
                msg,
            ) = ServerChangeAttributesValidationHelper.valid_group_environment(
                new_group,
                Environment.objects.get(id=new_env_id),
            )
            if not is_valid:
                validation_errors.append(msg)
    return validation_errors
