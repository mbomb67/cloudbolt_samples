"""
CloudBolt build plugin for creating Azure Enterprise Agreement (EA) subscriptions
within a single tenant (same-boundary).

This plugin:
- Creates a subscription billed to an EA enrollment account in the SAME tenant as
  the chosen AzureARMHandler's service principal (no cross-tenant transfer).
- Omits `additionalProperties` from the alias PUT in the same-tenant case, so the
  calling SP automatically becomes the subscription Owner and the May-2026
  `blockSubscriptionsIntoTenant` tenant policy is not evaluated (no transfer occurs).
- Optionally places the subscription in a management group.
- Optionally assigns a human Owner (via owner_email) through an RBAC role
  assignment after the subscription is active, so the SP is not the sole owner.
- Writes the same `azure_subscription_*` custom fields the MCA build writes, so the
  shared discover / teardown / grant / revoke / list / apply-policy plugins operate
  on EA-created subscriptions without modification.

API grounding (Microsoft Learn, verified 2026-05):
- Alias create/get: Microsoft.Subscription/aliases, api-version 2021-10-01.
  https://learn.microsoft.com/en-us/rest/api/subscription/alias/create?view=rest-subscription-2021-10-01
- EA billing scope shape: /providers/Microsoft.Billing/billingAccounts/{ea}/enrollmentAccounts/{ea_acct}
  https://learn.microsoft.com/en-us/azure/cost-management-billing/manage/programmatically-create-subscription-enterprise-agreement
- Billing accounts / enrollment accounts list: api-version 2024-04-01.
  https://learn.microsoft.com/en-us/rest/api/billing/billing-accounts/list?view=rest-billing-2024-04-01

Requirements:
- One AzureARMHandler whose SP has `SubscriptionCreator` (role definition id
  a0bcee42-bf30-4d1b-926a-48d21664ef71) at the enrollment-account scope, plus a
  billing-read role (e.g. Enrollment Reader) at the billing-account scope so the
  dropdowns populate. See PREREQUISITES.md.
- For optional owner_email handoff: Graph User.Read.All on the SP and
  User Access Administrator / Owner at subscription scope.
"""

import json
import time
import uuid
from typing import Optional

from accounts.models import Group
from common.methods import set_progress
from c2_wrapper import create_custom_field
from infrastructure.models import Environment
from resourcehandlers.azure_arm.models import AzureARMHandler
from utilities.logger import ThreadLogger

from shared_modules.azure_subscription_helpers import (
    AZURE_MGMT_ENDPOINT,
    add_subscription_owner,
    azure_api_call,
    format_azure_http_error,
    get_azure_token,
    get_user_object_id_by_email,
    safe_message,
)

import requests

logger = ThreadLogger(__name__)

# API versions -- isolated as module constants so they are trivial to bump if MS
# Learn moves to a newer stable version (see plan Risks).
ALIAS_API_VERSION = "2021-10-01"
BILLING_API_VERSION = "2024-04-01"
SUBSCRIPTION_API_VERSION = "2022-12-01"

# Alias provisioning poll cadence (mirrors the MCA build defaults).
POLL_INTERVAL_SECONDS = 10
POLL_MAX_ATTEMPTS = 30

# Subscription-active validation poll cadence.
VALIDATION_INTERVAL_SECONDS = 10
VALIDATION_MAX_ATTEMPTS = 24

# Action Input names -- used both as template variables and as the unrendered-
# template sentinel set (CloudBolt leaves "{{ name }}" -> "name" when a value is
# absent for an optional field).
_PARAM_NAMES = {
    "subscription_name",
    "azure_handler",
    "billing_account",
    "enrollment_account",
    "management_group",
    "owner_email",
}


def create_custom_fields():
    """Pre-create the subscription metadata custom fields (shared with the MCA blueprint)."""
    create_custom_field("azure_subscription_id", "Azure Subscription ID", "STR")
    create_custom_field("azure_subscription_name", "Subscription Name", "STR")
    create_custom_field("azure_subscription_tenant_id", "Subscription Tenant ID", "STR")
    create_custom_field("azure_subscription_state", "Subscription State", "STR")
    create_custom_field("azure_subscription_billing_scope", "Billing Scope", "STR")
    create_custom_field("azure_subscription_management_group", "Management Group", "STR")
    create_custom_field("azure_subscription_rh_id", "Resource Handler ID", "STR")
    create_custom_field(
        "azure_subscription_source_rh_id", "Source RH ID (for alias cleanup)", "STR"
    )


def _is_valid_param(value: Optional[str]) -> bool:
    """True when value is non-empty and not an unrendered template literal."""
    if not value:
        return False
    if value in _PARAM_NAMES:
        return False
    return True


def _tenant_id_for(rh) -> Optional[str]:
    return getattr(rh, "azure_tenant_id", None) or getattr(rh, "tenant_id", None)


def _get_rh_by_id(rh_id, group_name):
    """
    Find an Azure Resource Handler by ID, validated against the group's available
    environments so the caller cannot reach a handler the group has no access to.
    """
    try:
        group = Group.objects.get(name=group_name)
    except Group.DoesNotExist:
        return None

    for env in group.get_available_environments():
        if str(env.resource_handler.id) == str(rh_id):
            return AzureARMHandler.objects.get(id=rh_id)
    return None


def _azure_handler_options(group_name):
    """Build the unique (rh_id, display) list of Azure handlers the group can use."""
    try:
        group = Group.objects.get(name=group_name)
    except Group.DoesNotExist:
        return []

    azure_envs = [
        env
        for env in group.get_available_environments()
        if env.resource_handler.resource_technology
        and env.resource_handler.resource_technology.name == "Azure"
    ]

    seen = set()
    options = []
    for env in azure_envs:
        rh = env.resource_handler.cast()
        rh_id = str(rh.id)
        if rh_id in seen:
            continue
        seen.add(rh_id)
        tenant_id = _tenant_id_for(rh)
        display = f"{rh.name} ({tenant_id})" if tenant_id else rh.name
        options.append((rh_id, display))

    options.sort(key=lambda x: x[1])
    return options


def generate_options_for_azure_handler(**kwargs):
    """Single group-scoped Azure Resource Handler dropdown (same-boundary: one tenant)."""
    group_name = kwargs.get("group")
    if not group_name:
        return []

    options = _azure_handler_options(group_name)
    if not options:
        return [("", "--- No Azure Resource Handlers available ---")]
    options.insert(0, ("", "------ Please Select an Azure Tenant ------"))
    return options


def generate_options_for_billing_account(field, control_value=None, control_value_dict=None, **kwargs):
    """
    List EA billing accounts visible to the selected handler's SP.
    Depends on: azure_handler (RH id). Filters to agreementType == EnterpriseAgreement.
    """
    if not control_value_dict:
        return [("", "------ Select an Azure tenant first ------")]

    rh_id = control_value_dict.get("azure_handler")
    group_name = kwargs.get("group")
    if not rh_id:
        return [("", "------ Select an Azure tenant first ------")]
    if not group_name:
        return [("", "------ Error: No group specified ------")]

    rh = _get_rh_by_id(rh_id, group_name)
    if not rh:
        return [("", "------ Error: Resource handler not found ------")]

    tenant_id = _tenant_id_for(rh)
    try:
        token = get_azure_token(tenant_id, rh.client_id, rh.secret)
        url = f"{AZURE_MGMT_ENDPOINT}/providers/Microsoft.Billing/billingAccounts"
        response = azure_api_call("GET", url, token, params={"api-version": BILLING_API_VERSION})

        options = []
        for account in response.get("value", []) or []:
            props = account.get("properties", {}) or {}
            if props.get("agreementType") != "EnterpriseAgreement":
                continue
            account_id = account.get("id")
            display = props.get("displayName") or account.get("name")
            options.append((account_id, f"{display} (EA)"))

        options.sort(key=lambda x: x[1])
        if not options:
            return [("", "------ No EA billing accounts found ------")]
        return options
    except Exception as e:
        logger.exception(f"Failed to list EA billing accounts: {e}")
        return [("", f"------ Error: {safe_message(str(e))} ------")]


def generate_options_for_enrollment_account(field, control_value=None, control_value_dict=None, **kwargs):
    """
    List enrollment accounts under the chosen EA billing account.
    Depends on: azure_handler (RH id) + billing_account (billing account id).
    The option value is the full enrollment-account id, which feeds billingScope directly.
    """
    if not control_value_dict:
        return [("", "------ Select a billing account first ------")]

    rh_id = control_value_dict.get("azure_handler")
    billing_account = control_value_dict.get("billing_account")
    group_name = kwargs.get("group")

    if not rh_id or not billing_account:
        return [("", "------ Select an Azure tenant and billing account first ------")]
    if not group_name:
        return [("", "------ Error: No group specified ------")]

    rh = _get_rh_by_id(rh_id, group_name)
    if not rh:
        return [("", "------ Error: Resource handler not found ------")]

    tenant_id = _tenant_id_for(rh)
    try:
        token = get_azure_token(tenant_id, rh.client_id, rh.secret)
        url = f"{AZURE_MGMT_ENDPOINT}{billing_account}/enrollmentAccounts"
        response = azure_api_call("GET", url, token, params={"api-version": BILLING_API_VERSION})

        options = []
        for ea in response.get("value", []) or []:
            ea_id = ea.get("id")
            props = ea.get("properties", {}) or {}
            display = props.get("displayName") or props.get("accountName") or ea.get("name")
            owner = props.get("accountOwner") or props.get("accountOwnerEmail")
            label = f"{display} [{owner}]" if owner else display
            options.append((ea_id, label))

        options.sort(key=lambda x: x[1])
        if not options:
            return [("", "------ No enrollment accounts found ------")]
        return options
    except Exception as e:
        logger.exception(f"Failed to list enrollment accounts: {e}")
        return [("", f"------ Error: {safe_message(str(e))} ------")]


def generate_options_for_management_group(field, control_value=None, control_value_dict=None, **kwargs):
    """
    List management groups in the selected handler's tenant (optional placement).
    Depends on: azure_handler (RH id).
    """
    if not control_value_dict:
        return [("", "------ Select an Azure tenant first ------")]

    rh_id = control_value_dict.get("azure_handler")
    group_name = kwargs.get("group")
    if not rh_id:
        return [("", "------ Select an Azure tenant first ------")]
    if not group_name:
        return [("", "------ Error: No group specified ------")]

    rh = _get_rh_by_id(rh_id, group_name)
    if not rh:
        return [("", "------ Error: Resource handler not found ------")]

    tenant_id = _tenant_id_for(rh)
    try:
        token = get_azure_token(tenant_id, rh.client_id, rh.secret)
        url = f"{AZURE_MGMT_ENDPOINT}/providers/Microsoft.Management/managementGroups"
        response = azure_api_call("GET", url, token, params={"api-version": "2020-05-01"})

        options = [("", "------ None (root) ------")]
        for mg in response.get("value", []) or []:
            mg_id = mg.get("name")  # management group ID
            display = mg.get("properties", {}).get("displayName", mg_id)
            options.append((mg_id, display))
        return options
    except Exception as e:
        logger.exception(f"Failed to list management groups: {e}")
        return [("", f"------ Error: {safe_message(str(e))} ------")]


def run(job, **kwargs):
    """Build entry point: create an EA subscription in the SP's home tenant."""
    set_progress("Starting Azure EA subscription creation (same-boundary)...")
    logger.info("Starting Azure EA subscription build for job %s", job.id)

    create_custom_fields()

    subscription_name = "{{ subscription_name }}".strip()
    rh_id = "{{ azure_handler }}".strip()
    billing_account = "{{ billing_account }}".strip()
    enrollment_account = "{{ enrollment_account }}".strip()  # full id -> billingScope
    management_group = "{{ management_group }}".strip()
    owner_email = "{{ owner_email }}".strip()

    # Validate required parameters.
    if not _is_valid_param(subscription_name):
        return "FAILURE", "Subscription name is required", ""
    if not _is_valid_param(rh_id):
        return "FAILURE", "Azure tenant (resource handler) is required", ""
    if not _is_valid_param(enrollment_account):
        return "FAILURE", "Enrollment account (billing scope) is required", ""

    # Normalize optional parameters.
    if not _is_valid_param(management_group):
        management_group = None
    if not _is_valid_param(owner_email):
        owner_email = None

    # Resolve group from the resource/order.
    resource = kwargs.get("resource") or job.resource_set.first()
    if not resource or not resource.group:
        return "FAILURE", "Unable to determine group from job", ""
    group_name = resource.group.name

    rh = _get_rh_by_id(rh_id, group_name)
    if not rh:
        return "FAILURE", f"Unable to find Azure Resource Handler (ID: {rh_id})", ""

    tenant_id = _tenant_id_for(rh)
    client_id = rh.client_id
    client_secret = rh.secret

    logger.info(
        "EA subscription params - Name: %s, RH: %s, Tenant: %s, EnrollmentAcct: %s, MG: %s, Owner: %s",
        subscription_name, rh.name, tenant_id, enrollment_account, management_group or "None", owner_email or "None",
    )
    set_progress(
        f"Creating EA subscription '{subscription_name}' in tenant {tenant_id} "
        f"billed to {enrollment_account}"
    )

    try:
        token = get_azure_token(tenant_id, client_id, client_secret)

        # Create the subscription alias.
        alias_name = str(uuid.uuid4())
        alias_url = f"{AZURE_MGMT_ENDPOINT}/providers/Microsoft.Subscription/aliases/{alias_name}"
        params = {"api-version": ALIAS_API_VERSION}

        # Same-tenant case: OMIT additionalProperties so the SP auto-becomes owner and
        # no transfer-into-tenant semantics (blockSubscriptionsIntoTenant) are triggered.
        # See MS Learn: programmatically-create-subscription-enterprise-agreement.
        payload = {
            "properties": {
                "displayName": subscription_name,
                "billingScope": enrollment_account,
                "workload": "Production",
            }
        }
        if management_group:
            # ONLY managementGroupId -- never subscriptionTenantId/subscriptionOwnerId,
            # which would flip this into the cross-tenant transfer flow.
            payload["properties"]["additionalProperties"] = {
                "managementGroupId": (
                    f"/providers/Microsoft.Management/managementGroups/{management_group}"
                )
            }

        set_progress("Creating subscription via Alias API...")
        try:
            response = azure_api_call("PUT", alias_url, token, data=payload, params=params)
        except requests.exceptions.HTTPError as e:
            status = getattr(e.response, "status_code", None)
            if status == 409:
                return (
                    "FAILURE",
                    "Subscription alias already exists (409). This indicates a stale "
                    "alias from a previous failed run; run teardown to clean up the "
                    "stuck alias, then retry. "
                    f"Details: {format_azure_http_error(e)}",
                    "",
                )
            return "FAILURE", f"Alias creation failed: {format_azure_http_error(e)}", ""

        # Poll for provisioning completion (alias PUT returns 200 done / 201 in progress).
        provisioning_state = (response.get("properties") or {}).get("provisioningState")
        if provisioning_state != "Succeeded":
            set_progress("Subscription creation accepted; polling for completion...")
            for attempt in range(POLL_MAX_ATTEMPTS):
                time.sleep(POLL_INTERVAL_SECONDS)
                poll = azure_api_call("GET", alias_url, token, params=params)
                provisioning_state = (poll.get("properties") or {}).get("provisioningState")
                if provisioning_state == "Succeeded":
                    response = poll
                    break
                if provisioning_state == "Failed":
                    reason = (poll.get("properties") or {}).get(
                        "errorDetails", "Unknown error"
                    )
                    return "FAILURE", f"Subscription creation failed: {safe_message(json.dumps(reason))}", ""
                set_progress(
                    f"Polling subscription creation... ({attempt + 1}/{POLL_MAX_ATTEMPTS}, "
                    f"state: {provisioning_state})"
                )
            else:
                return (
                    "WARNING",
                    "Subscription creation timed out while polling the alias. The "
                    "subscription may still be created in Azure; check the alias state "
                    f"manually (alias name: {alias_name}).",
                    "",
                )

        subscription_id = (response.get("properties") or {}).get("subscriptionId")
        if not subscription_id:
            return (
                "FAILURE",
                "Subscription created but no subscriptionId in the alias response: "
                f"{safe_message(json.dumps(response))}",
                "",
            )

        set_progress(f"Subscription created: {subscription_id}")

        # Persist core metadata immediately so teardown can clean up even if a later
        # step fails. source_rh_id == rh_id for same-boundary EA (see plan KTD).
        resource.set_value_for_custom_field("azure_subscription_id", subscription_id)
        resource.set_value_for_custom_field("azure_subscription_name", subscription_name)
        resource.set_value_for_custom_field("azure_subscription_tenant_id", tenant_id)
        resource.set_value_for_custom_field("azure_subscription_rh_id", str(rh.id))
        resource.set_value_for_custom_field("azure_subscription_source_rh_id", str(rh.id))
        resource.save()
        set_progress("Subscription metadata saved (enables cleanup if later steps fail)")

        # Validate the subscription reaches an active state.
        set_progress("Validating subscription is active...")
        sub_state = None
        sub_url = f"{AZURE_MGMT_ENDPOINT}/subscriptions/{subscription_id}"
        sub_params = {"api-version": SUBSCRIPTION_API_VERSION}
        for attempt in range(VALIDATION_MAX_ATTEMPTS):
            try:
                check = azure_api_call("GET", sub_url, token, params=sub_params)
                if check.get("error") == "NotFound":
                    sub_state = None
                else:
                    sub_state = check.get("state")
                    if sub_state in ("Enabled", "Warned", "PastDue"):
                        set_progress(f"Subscription is active (state: {sub_state})")
                        break
            except Exception as e:
                logger.warning(f"Error checking subscription state: {e}")
            if attempt < VALIDATION_MAX_ATTEMPTS - 1:
                time.sleep(VALIDATION_INTERVAL_SECONDS)

        actual_state = sub_state or "Unknown"
        resource.set_value_for_custom_field("azure_subscription_state", actual_state)
        resource.set_value_for_custom_field("azure_subscription_billing_scope", enrollment_account)
        resource.set_value_for_custom_field(
            "azure_subscription_management_group", management_group or ""
        )
        resource.name = subscription_name
        resource.save()

        # Optional human-Owner handoff via RBAC (R9). Failures here are WARNINGs --
        # the subscription itself was created successfully.
        owner_warning = None
        if owner_email and actual_state in ("Enabled", "Warned", "PastDue"):
            set_progress(f"Assigning {owner_email} as subscription Owner via RBAC...")
            user_object_id, lookup_error = get_user_object_id_by_email(
                owner_email, tenant_id, client_id, client_secret
            )
            if lookup_error:
                owner_warning = f"Owner not assigned: {lookup_error}"
                logger.warning(owner_warning)
            else:
                ok, assign_error = add_subscription_owner(subscription_id, user_object_id, token)
                if ok:
                    set_progress(f"Assigned {owner_email} as Owner of {subscription_id}")
                else:
                    owner_warning = f"Owner assignment for {owner_email} failed: {assign_error}"
                    logger.warning(owner_warning)
        elif owner_email:
            owner_warning = (
                f"Subscription not active (state: {actual_state}); skipped Owner "
                f"assignment for {owner_email}. Assign Owner manually once active."
            )
            logger.warning(owner_warning)

        # Build the success message.
        msg = (
            f"Azure EA subscription '{subscription_name}' created.\n"
            f"Subscription ID: {subscription_id}\n"
            f"Tenant: {tenant_id}\n"
            f"Billing scope: {enrollment_account}\n"
            f"Management group: {management_group or 'None'}\n"
            f"State: {actual_state}\n"
        )
        if owner_email and not owner_warning:
            msg += f"Owner: {owner_email} assigned via RBAC role assignment.\n"
        msg += (
            f"\nNote: service principal {client_id} is currently Owner of this "
            f"subscription. To reduce blast radius, remove that role assignment after "
            f"verifying the intended Owner can manage the subscription."
        )

        if owner_warning:
            msg += f"\n\nWARNING: {owner_warning}"
            return "WARNING", msg, ""

        if actual_state not in ("Enabled", "Warned", "PastDue"):
            msg += (
                f"\n\nWARNING: subscription did not reach an active state within "
                f"{VALIDATION_MAX_ATTEMPTS * VALIDATION_INTERVAL_SECONDS}s "
                f"(current: {actual_state})."
            )
            return "WARNING", msg, ""

        logger.info(msg)
        return "SUCCESS", msg, ""

    except requests.exceptions.HTTPError as e:
        error_msg = f"Azure API error: {format_azure_http_error(e)}"
        logger.exception(error_msg)
        return "FAILURE", error_msg, ""
    except Exception as e:
        error_msg = f"Unexpected error creating subscription: {safe_message(str(e))}"
        logger.exception(error_msg)
        return "FAILURE", error_msg, ""
