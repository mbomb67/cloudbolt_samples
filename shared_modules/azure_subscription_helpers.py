"""
CloudBolt shared module: Azure subscription / RBAC / Policy REST helpers.

Used by the plugins under /var/opt/cloudbolt/proserv/azure/subscription/:

- discover_azure_subscriptions.py
- grant_subscription_access.py
- revoke_subscription_access.py
- list_subscription_access.py
- apply_public_exposure_policy.py

Per AGENTS.md, shared modules live here (the global
/var/www/html/cloudbolt/static/uploads/shared_modules/ location) and are
imported as `from shared_modules.azure_subscription_helpers import ...`.

This module is intentionally framework-light: direct `requests` against the
Azure Management API and Microsoft Graph, matching the build plugin's no-SDK
posture. No CloudBolt model imports here -- the calling plugins handle
ResourceHandler resolution.
"""

import fnmatch
import uuid
from typing import Dict, List, Optional, Tuple

import requests

from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

AZURE_MGMT_ENDPOINT = "https://management.azure.com"
AZURE_LOGIN_ENDPOINT = "https://login.microsoftonline.com"
AZURE_GRAPH_ENDPOINT = "https://graph.microsoft.com/v1.0"


def safe_message(text: str) -> str:
    """
    Sanitize text for inclusion in a CloudBolt action message.

    CloudBolt's synchronous resource-action path passes the action's
    returned message through Django's format_html(), which internally
    calls str.format() on it. Any literal '{' or '}' in the message
    (typical for raw Azure JSON error bodies like {"error": {...}}) is
    interpreted as a format-spec placeholder and crashes with KeyError
    or IndexError before the user ever sees the message. Strip braces
    rather than escape (escaping with {{ / }} only works if the consumer
    is .format()ing AND we control the consumer; stripping is safe in
    all cases and produces readable output).
    """
    if not text:
        return ""
    return text.replace("{", "(").replace("}", ")")


def format_azure_http_error(exc: requests.exceptions.HTTPError) -> str:
    """
    Convert a requests HTTPError from an Azure API call into a one-line
    summary safe for inclusion in a CloudBolt action message.

    Azure error responses follow the shape
        {"error": {"code": "...", "message": "..."}}
    so we try to extract code+message first. If the body is not JSON or
    not in that shape, fall back to a brace-stripped truncation of the
    raw text (see safe_message above for why braces matter).
    """
    response = getattr(exc, "response", None)
    if response is None:
        return f"HTTP error: {safe_message(str(exc))}"
    status = response.status_code
    body = response.text or ""
    try:
        parsed = response.json()
    except ValueError:
        parsed = None
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            code = err.get("code") or ""
            message = err.get("message") or ""
            if code and message:
                return f"HTTP {status} {code}: {safe_message(message)}"
            if code or message:
                return f"HTTP {status} -- {safe_message(code or message)}"
    return f"HTTP {status} -- {safe_message(body)[:500]}"


def get_azure_token(
    tenant_id: str,
    client_id: str,
    client_secret: str,
    resource: str = "https://management.azure.com/",
) -> str:
    """Acquire an OAuth2 token for the given tenant/SP via client_credentials."""
    token_url = f"{AZURE_LOGIN_ENDPOINT}/{tenant_id}/oauth2/token"
    payload = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "resource": resource,
    }
    response = requests.post(token_url, data=payload)
    response.raise_for_status()
    return response.json()["access_token"]


def azure_api_call(
    method: str,
    url: str,
    token: str,
    data: Optional[Dict] = None,
    params: Optional[Dict] = None,
) -> Dict:
    """
    Authenticated call to an Azure REST endpoint. Returns parsed JSON or {}.

    A 404 response is returned as {"error": "NotFound", "status_code": 404}
    rather than raising, so callers can treat "missing resource" as a
    flow-control signal (idempotent paths). All other 4xx/5xx responses raise
    requests.exceptions.HTTPError, which the caller is expected to catch and
    map to a CloudBolt FAILURE / WARNING.
    """
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    response = requests.request(
        method=method, url=url, headers=headers, json=data, params=params
    )
    if response.status_code == 404:
        return {"error": "NotFound", "status_code": 404}
    if response.status_code == 202:
        return {"status": "Accepted", "location": response.headers.get("Location")}
    response.raise_for_status()
    if response.content:
        return response.json()
    return {}


def get_user_object_id_by_email(
    email: str,
    tenant_id: str,
    client_id: str,
    client_secret: str,
) -> Tuple[Optional[str], Optional[str]]:
    """
    Look up an Azure AD user's Object ID by email. Hardened relative to the
    build plugin's helper:

    - On zero matches -> (None, "no user found ...").
    - On multiple matches -> (None, "multiple users matched ...") with each
      candidate's id/UPN/mail/userType so the operator can disambiguate.
      Never silently picks users[0].
    - On a single match where userType == "Guest" -> (None, "B2B/guest users
      not supported in v1 ...").

    Returns (object_id, None) on success; (None, error_message) otherwise.
    """
    try:
        graph_token = get_azure_token(
            tenant_id, client_id, client_secret, resource="https://graph.microsoft.com/"
        )
    except requests.exceptions.HTTPError as exc:
        return None, f"Microsoft Graph token acquisition failed: {format_azure_http_error(exc)}"
    except Exception as exc:
        return None, f"Microsoft Graph token acquisition failed: {safe_message(str(exc))}"

    graph_url = f"{AZURE_GRAPH_ENDPOINT}/users"
    params = {
        "$filter": f"userPrincipalName eq '{email}' or mail eq '{email}'",
        "$select": "id,userPrincipalName,mail,userType,accountEnabled",
    }
    headers = {"Authorization": f"Bearer {graph_token}"}

    try:
        response = requests.get(graph_url, headers=headers, params=params)
        response.raise_for_status()
    except requests.exceptions.HTTPError as exc:
        return None, f"Microsoft Graph user lookup failed: {format_azure_http_error(exc)}"
    except Exception as exc:
        return None, f"Microsoft Graph user lookup failed: {safe_message(str(exc))}"

    users = response.json().get("value", [])

    if not users:
        return None, f"No user found in Azure AD for email '{email}'."

    if len(users) > 1:
        candidate_lines = [
            f"  - id={u.get('id')} upn={u.get('userPrincipalName')} "
            f"mail={u.get('mail')} userType={u.get('userType')} "
            f"accountEnabled={u.get('accountEnabled')}"
            for u in users
        ]
        return None, (
            f"Multiple users in Azure AD matched email '{email}'. "
            f"Refusing to pick one; resolve the ambiguity in Azure AD or "
            f"supply a unique identifier. Candidates:\n" + "\n".join(candidate_lines)
        )

    user = users[0]
    user_type = user.get("userType")
    if user_type == "Guest":
        return None, (
            f"User '{email}' has userType=Guest. B2B / external guest users "
            f"are not supported in v1; grant access to a member account or "
            f"use the Azure portal."
        )

    return user.get("id"), None


def check_subscription_permissions(
    subscription_id: str,
    required_actions: List[str],
    token: str,
) -> Tuple[List[str], Optional[str]]:
    """
    Probe the calling principal's effective permissions at subscription scope
    and return any required actions that are NOT granted.

    Uses GET /subscriptions/{sub}/providers/Microsoft.Authorization/permissions
    which returns the union of role assignments at subscription scope and
    above (management group, tenant root). Each entry has actions/notActions
    glob patterns; an action is granted iff some entry's actions matches AND
    no entry's notActions matches.

    Returns:
        ([], None)            -- all required actions are granted
        ([missing...], None)  -- some required actions are not granted
        (required, error)     -- API call failed; treat all as missing
    """
    url = f"{AZURE_MGMT_ENDPOINT}/subscriptions/{subscription_id}/providers/Microsoft.Authorization/permissions"
    params = {"api-version": "2022-04-01"}
    try:
        response = azure_api_call("GET", url, token, params=params)
    except requests.exceptions.HTTPError as exc:
        return list(required_actions), f"Permission probe failed: {format_azure_http_error(exc)}"
    except Exception as exc:
        return list(required_actions), f"Permission probe failed: {safe_message(str(exc))}"

    entries = response.get("value", [])

    def _granted(action: str) -> bool:
        granted_anywhere = False
        for entry in entries:
            allowed = entry.get("actions", []) or []
            denied = entry.get("notActions", []) or []
            if any(fnmatch.fnmatchcase(action, pat) for pat in allowed):
                if not any(fnmatch.fnmatchcase(action, pat) for pat in denied):
                    granted_anywhere = True
                    break
        return granted_anywhere

    missing = [a for a in required_actions if not _granted(a)]
    return missing, None


# Built-in Azure RBAC "Owner" role definition ID (constant across all tenants).
OWNER_ROLE_DEFINITION_ID = "8e3af657-a8ff-443c-a75c-2fe8c4bcb635"

ROLE_ASSIGNMENT_API_VERSION = "2022-04-01"


def add_subscription_owner(
    subscription_id: str,
    user_object_id: str,
    token: str,
) -> Tuple[bool, Optional[str]]:
    """
    Assign the built-in Owner role to a user (principalType User) at subscription
    scope via an RBAC role assignment.

    A fresh role-assignment GUID is generated per call. A 409 from Azure means the
    principal already holds this role at this scope -- the desired end state -- so
    it is treated as success.

    Returns:
        (True, None)   -- role assigned, or already present (409)
        (False, error) -- any other failure, with a CloudBolt-safe error message
    """
    role_assignment_id = str(uuid.uuid4())
    url = (
        f"{AZURE_MGMT_ENDPOINT}/subscriptions/{subscription_id}"
        f"/providers/Microsoft.Authorization/roleAssignments/{role_assignment_id}"
    )
    params = {"api-version": ROLE_ASSIGNMENT_API_VERSION}
    payload = {
        "properties": {
            "roleDefinitionId": (
                f"/subscriptions/{subscription_id}/providers/"
                f"Microsoft.Authorization/roleDefinitions/{OWNER_ROLE_DEFINITION_ID}"
            ),
            "principalId": user_object_id,
            "principalType": "User",
        }
    }
    try:
        azure_api_call("PUT", url, token, data=payload, params=params)
        return True, None
    except requests.exceptions.HTTPError as exc:
        if getattr(exc.response, "status_code", None) == 409:
            return True, None
        return False, format_azure_http_error(exc)
    except Exception as exc:
        return False, safe_message(str(exc))
