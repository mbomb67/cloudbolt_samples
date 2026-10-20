"""
Stamps every audit record written during an MCP tool call with WHICH AGENT
(the OAuth client, e.g. "Claude") and WHICH SESSION (the MCP connection)
acted, so one agent action can be traced end to end: MCP request log line ->
Order history -> Job -> server / resource history.

Problem:
    ``cb_mcp.core.log_mcp_request`` logs ``<correlation> <username> <tool>``
    and every Order, Job and history row created by an MCP call names only the
    user. Nothing records that an AI agent, rather than the person, drove the
    action, nor which conversation it belonged to. When several agents share
    one user's OAuth grant, or one agent runs many sessions, nothing in the
    audit trail distinguishes them.

What it records:
    agent    OAuth ``Application.name`` of the client holding the bearer token
             (the dynamic-client-registration client_name). Resolved per request
             by hashing the bearer string and reading the django-oauth-toolkit
             ``AccessToken`` row (``token_checksum``, unique + indexed in DOT
             3.2.0). Falls back to the MCP ``initialize`` clientInfo name, then
             the HTTP User-Agent, then "unknown". Token issuance is NOT changed,
             so tokens issued before this patch are attributed too.
    client   ``Application.client_id`` ("" when unresolved).
    session  12 hex chars = sha256(``Mcp-Session-Id`` header)[:12]. django-mcp-
             server 0.5.7 runs stateful: that header is required on every call
             after ``initialize`` and is backed by a Django session row, so it
             identifies one agent connection across many tool calls. The JWT
             ``jti`` is NOT used as the session: SimpleJWT mints these tokens
             with a 5 minute lifetime (settings.SIMPLE_JWT), so jti rotates
             every 5 minutes. jti is still written to the log line.

The marker (contract shared with the Agent Activity UI extension):
    [mcp agent="<agent>" client="<client_id>" session="<sid>"]
    Field order is fixed; values are double-quoted with \\ and \" escaped.

Where it lands:
    * /var/log/cloudbolt/mcp.log: the existing per-call line gains
      ``agent="..." client="..." session="..." jti="..." client_info="..."``
      between the tool name and ``args=``; the completed/failed lines share the
      same prefix.
    * OrderHistory / ServerHistory / ResourceHistory / every other
      ``HistoryModel`` subclass: the marker is appended to ``event_message`` on
      rows created while attribution is known.
    * Job: a ``ProgressMessage`` starting with the marker is created with each
      Job. Job has no free-form field: ``label`` is its display name, ``labels``
      drives job-engine host selection, and ``outputs`` is overwritten by child
      jobs, so the first progress line is the only safe place.

Hooks (applied in this order by the kit runner; each is idempotent and never
raises out of apply, so a failure disables one hook rather than httpd):
    a. Tools: every public method on every ``CbMcpToolset`` subclass is
       re-wrapped, replacing the ``log_mcp_request`` layer with a copy that
       logs attribution and sets a ContextVar for the duration of the call.
       Safe because mcp_server's ``_ToolsetMethodCaller`` resolves the method
       with ``getattr`` at call time, and first-class action tools call the
       same methods. Also wraps ``DjangoMCP.handle_django_request`` to copy
       the ``initialize`` clientInfo into the MCP session.
    b. History: ``HistoryModel.save`` appends the marker when adding a row and
       attribution is known, directly from the ContextVar or, inside the job
       engine, inherited from the row's job tree or order (tier 3).
    c. Jobs: a ``post_save(Job, created=True)`` receiver writes the
       ProgressMessage, inheriting from the parent job inside the job engine.
    d. Internal API hop: ``approve_order`` / ``cancel_order`` / ``deny_order``
       reach the DB through ``InternalAPIClient`` -> HTTP -> /api/v3/ in
       another worker. ``InternalAPIClient.__init__`` is wrapped to mint its
       JWT with signed ``mcp_agent`` / ``mcp_client`` / ``mcp_session`` claims
       when attribution is known, and ``JWTAuthentication.authenticate`` is
       wrapped to read those claims back into the ContextVar for that request;
       ``request_started`` / ``request_finished`` clear it. Claims are signed
       with UNIQUE_TOKEN, so an outside caller cannot forge attribution.

Tier-3 inheritance only runs inside a job thread (``get_thread_job()`` is
set), so a human re-running or approving an agent's work in the UI is never
mislabelled as the agent.

Deploy:
    Copy to PROSERV_DIR/patch_kit/patch_sets/ with the patch_kit app in
    INSTALLED_APPS (customer_settings.py), then ``systemctl restart httpd``
    AND ``systemctl restart jobengine`` (hooks b and c must load in the job
    engine for inherited attribution). Remove the file and restart both to
    revert; existing markers stay in the rows they were written to.

Written against CloudBolt source as of 2026-10 with django-mcp-server 0.5.7,
django-oauth-toolkit 3.2.0 and djangorestframework-simplejwt 5.5.0. If an
upgrade changes ``cb_mcp.core.log_mcp_request``, remove or re-verify hook a.
"""

import contextvars
import functools
import hashlib
import re
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Optional

from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)
# Same logger name cb_mcp.core uses, so the per-call lines keep landing in
# the mcp_logfile handler (/var/log/cloudbolt/mcp.log).
mcp_logger = ThreadLogger("mcp.tools")

MARKER_PREFIX = "[mcp agent="
MARKER_RE = re.compile(
    r'\[mcp agent="((?:[^"\\]|\\.)*)" client="((?:[^"\\]|\\.)*)" '
    r'session="([0-9a-f]{0,12})"\]'
)
PROGRESS_SUFFIX = " Job initiated through the CloudBolt MCP server."

CLAIM_AGENT = "mcp_agent"
CLAIM_CLIENT = "mcp_client"
CLAIM_SESSION = "mcp_session"
SESSION_KEY_CLIENT_INFO = "ps008_mcp_client_info"

AGENT_MAX_LEN = 80
USER_AGENT_MAX_LEN = 60
SESSION_DIGEST_LEN = 12
CACHE_MAX = 512
MAX_PARENT_HOPS = 20

_current = contextvars.ContextVar("ps008_mcp_attribution", default=None)

# checksum -> (agent, client_id) | None ; job id -> Attribution | None ;
# order id -> Attribution | None. Bounded, cleared when full.
_oauth_cache = {}
_job_cache = {}
_order_cache = {}
_cache_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Attribution value + marker contract
# --------------------------------------------------------------------------- #
def _escape(value) -> str:
    text = re.sub(r"[\r\n]+", " ", str(value or ""))
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _unescape(value: str) -> str:
    return re.sub(r"\\(.)", r"\1", value or "")


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:SESSION_DIGEST_LEN]


@dataclass(frozen=True)
class Attribution:
    agent: str
    client: str = ""
    session: str = ""
    jti: str = ""
    client_info: str = ""

    def marker(self) -> str:
        return (
            f'{MARKER_PREFIX}"{_escape(self.agent)}" '
            f'client="{_escape(self.client)}" session="{self.session}"]'
        )

    def progress_message(self) -> str:
        return self.marker() + PROGRESS_SUFFIX

    def log_fields(self) -> str:
        return (
            f'agent="{_escape(self.agent)}" client="{_escape(self.client)}" '
            f'session="{self.session}" jti="{_escape(self.jti)}" '
            f'client_info="{_escape(self.client_info)}"'
        )

    @classmethod
    def parse(cls, text) -> Optional["Attribution"]:
        match = MARKER_RE.search(text or "")
        if not match:
            return None
        return cls(
            agent=_unescape(match.group(1)),
            client=_unescape(match.group(2)),
            session=match.group(3),
        )


def current_attribution() -> Optional[Attribution]:
    """The attribution of the MCP call running in this thread, if any."""
    return _current.get()


def _bounded_put(cache: dict, key, value):
    with _cache_lock:
        if len(cache) >= CACHE_MAX:
            cache.clear()
        cache[key] = value


# --------------------------------------------------------------------------- #
# Resolution from the MCP request
# --------------------------------------------------------------------------- #
def _bearer_from(meta) -> Optional[str]:
    header = (meta or {}).get("HTTP_AUTHORIZATION", "")
    parts = header.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1]
    return None


def _lookup_oauth_app(raw_token: str):
    """(Application.name, client_id) for the DOT AccessToken row holding this
    bearer string, or None. One indexed query per distinct token, cached."""
    checksum = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    if checksum in _oauth_cache:
        return _oauth_cache[checksum]

    from oauth2_provider.models import get_access_token_model

    row = (
        get_access_token_model()
        .objects.select_related("application")
        .filter(token_checksum=checksum)
        .first()
    )
    found = None
    if row is not None and row.application is not None:
        found = (str(row.application.name or ""), str(row.application.client_id or ""))
    _bounded_put(_oauth_cache, checksum, found)
    return found


def _client_info_from_request(request) -> dict:
    """clientInfo stashed in the MCP Django session by hook a, or {}."""
    session = getattr(request, "session", None)
    getter = getattr(session, "get", None)
    if not callable(getter):
        return {}
    info = getter(SESSION_KEY_CLIENT_INFO)
    return info if isinstance(info, dict) else {}


def _resolve(request) -> Attribution:
    meta = getattr(request, "META", None) or {}
    auth = getattr(request, "auth", None)
    auth_get = getattr(auth, "get", None)
    jti = str(auth_get("jti") or "") if callable(auth_get) else ""

    agent = client = ""
    raw = _bearer_from(meta)
    if raw:
        found = _lookup_oauth_app(raw)
        if found:
            agent, client = found

    info = _client_info_from_request(request)
    client_info = ""
    if info.get("name"):
        client_info = f"{info.get('name')}/{info.get('version') or ''}".rstrip("/")

    if not agent:
        agent = (
            str(info.get("name") or "")
            or str(meta.get("HTTP_USER_AGENT") or "")[:USER_AGENT_MAX_LEN]
            or "unknown"
        )
    agent = agent[:AGENT_MAX_LEN]

    session_source = meta.get("HTTP_MCP_SESSION_ID") or jti
    session = _digest(str(session_source)) if session_source else ""

    return Attribution(
        agent=agent, client=client, session=session, jti=jti, client_info=client_info
    )


def resolve_attribution(request) -> Attribution:
    """Never raises: an attribution failure must not fail the tool call."""
    try:
        return _resolve(request)
    except Exception:
        logger.debug("ps_008: MCP attribution lookup failed", exc_info=True)
        return Attribution(agent="unknown")


# --------------------------------------------------------------------------- #
# Tier 3: inherit attribution inside the job engine
# --------------------------------------------------------------------------- #
def _in_job_thread() -> bool:
    try:
        from jobs.threadjob import get_thread_job

        return get_thread_job() is not None
    except Exception:
        return False


def attribution_for_order(order) -> Optional[Attribution]:
    """Marker on the order's earliest attributed history row, cached."""
    if order is None:
        return None
    if order.id in _order_cache:
        return _order_cache[order.id]

    from history.models import OrderHistory

    message = (
        OrderHistory.objects.filter(order=order, event_message__contains=MARKER_PREFIX)
        .order_by("id")
        .values_list("event_message", flat=True)
        .first()
    )
    found = Attribution.parse(message) if message else None
    _bounded_put(_order_cache, order.id, found)
    return found


def attribution_for_job(job) -> Optional[Attribution]:
    """Marker on this job, its ancestors' progress messages, or the top-level
    job's order; cached per job id."""
    if job is None:
        return None
    if job.id in _job_cache:
        return _job_cache[job.id]

    from jobs.models import ProgressMessage

    found = None
    chain = []
    current = job
    hops = 0
    while current is not None and hops <= MAX_PARENT_HOPS:
        chain.append(current)
        if current.id in _job_cache:
            found = _job_cache[current.id]
            break
        message = (
            ProgressMessage.objects.filter(
                job=current, message__startswith=MARKER_PREFIX
            )
            .order_by("id")
            .values_list("message", flat=True)
            .first()
        )
        if message:
            found = Attribution.parse(message)
            break
        current = current.parent_job
        hops += 1

    if found is None and chain:
        found = attribution_for_order(chain[-1].get_order())

    for visited in chain:
        _bounded_put(_job_cache, visited.id, found)
    return found


def _inherited_for_history(history) -> Optional[Attribution]:
    if not _in_job_thread():
        return None
    if getattr(history, "job_id", None):
        return attribution_for_job(history.job)
    if getattr(history, "order_id", None):
        return attribution_for_order(history.order)
    return None


def _inherited_for_new_job(job) -> Optional[Attribution]:
    if not _in_job_thread():
        return None
    from jobs.threadjob import get_thread_job

    source = job.parent_job if getattr(job, "parent_job_id", None) else get_thread_job()
    return attribution_for_job(source)


# --------------------------------------------------------------------------- #
# Hook a: MCP tool wrapper + clientInfo capture
# --------------------------------------------------------------------------- #
def _attributed_tool_wrapper(method, core):
    """Replacement for cb_mcp.core.log_mcp_request's wrapper: same log lines
    with attribution fields added, and the ContextVar set for the call."""

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        correlation_id = uuid.uuid4().hex
        request = getattr(self, "request", None)
        user = getattr(request, "user", None) if request is not None else None
        username = getattr(user, "username", None) or "unknown"
        attribution = resolve_attribution(request)
        msg = f"{correlation_id} {username} {method.__name__} {attribution.log_fields()}"
        safe_args = core._redact_secrets(args)
        safe_kwargs = core._redact_secrets(kwargs)
        mcp_logger.info(f"{msg} args={safe_args} kwargs={safe_kwargs}")
        start = time.monotonic()
        token = _current.set(attribution)
        try:
            result = method(self, *args, **kwargs)
            elapsed_ms = int((time.monotonic() - start) * 1000)
            mcp_logger.info(f"{msg} completed in {elapsed_ms}ms")
            return result
        except core.MCPScopeError as ex:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            mcp_logger.error(f"{msg} failed in {elapsed_ms}ms: {ex}")
            raise
        except Exception as ex:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            mcp_logger.exception(f"{msg} failed in {elapsed_ms}ms: {ex}")
            raise
        finally:
            _current.reset(token)

    wrapper._ps008_patched = True
    return wrapper


def _all_subclasses(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from _all_subclasses(sub)


def _rewrap_toolsets():
    import importlib

    from cb_mcp import core

    # mcp_server's ready() normally imports these before patch_kit's ready()
    # runs (INSTALLED_APPS order); importing again is a no-op and guarantees
    # every toolset subclass exists before we enumerate them.
    importlib.import_module("cb_mcp.mcp")

    for attr in ("CbMcpToolset", "log_mcp_request", "_redact_secrets", "MCPScopeError"):
        if not hasattr(core, attr):
            logger.error(
                f"ps_008: cb_mcp.core has no {attr}; MCP tool wrapper not applied."
            )
            return

    count = 0
    for cls in _all_subclasses(core.CbMcpToolset):
        for name, value in list(vars(cls).items()):
            if name.startswith("_") or not callable(value):
                continue
            if getattr(value, "_ps008_patched", False):
                continue
            inner = getattr(value, "__wrapped__", None)
            if inner is None:
                # Not wrapped by log_mcp_request (unexpected); still attribute.
                inner = value
            elif "correlation_id" not in getattr(
                getattr(value, "__code__", None), "co_varnames", ()
            ):
                logger.warning(
                    f"ps_008: {cls.__name__}.{name} log wrapper differs from the "
                    "version this patch was written against; re-verify after upgrade."
                )
            setattr(cls, name, _attributed_tool_wrapper(inner, core))
            count += 1
    logger.info(f"ps_008: attributed {count} MCP tool methods.")


def _client_info_from_initialize(request) -> Optional[dict]:
    try:
        data = getattr(request, "data", None)
        if not isinstance(data, dict) or data.get("method") != "initialize":
            return None
        info = (data.get("params") or {}).get("clientInfo") or {}
        if not info.get("name"):
            return None
        return {
            "name": str(info.get("name"))[:USER_AGENT_MAX_LEN],
            "version": str(info.get("version") or "")[:30],
        }
    except Exception:
        return None


def _wrap_handle_django_request():
    from mcp_server.djangomcp import DjangoMCP

    original = DjangoMCP.handle_django_request
    if getattr(original, "_ps008_patched", False):
        return

    @functools.wraps(original)
    def handle_django_request(self, request):
        client_info = _client_info_from_initialize(request)
        response = original(self, request)
        if client_info and not getattr(self, "stateless", False):
            # The session was created and saved inside the original call; the
            # response carries its key. Add clientInfo so later calls on this
            # connection can read it from request.session.
            try:
                key = response.get("Mcp-Session-Id") if hasattr(response, "get") else None
                if key:
                    session = self.SessionStore(key)
                    session[SESSION_KEY_CLIENT_INFO] = client_info
                    session.save()
            except Exception:
                logger.debug("ps_008: could not store MCP clientInfo", exc_info=True)
        return response

    handle_django_request._ps008_patched = True
    DjangoMCP.handle_django_request = handle_django_request


def patch_mcp_attribution_a_tools():
    try:
        _rewrap_toolsets()
    except Exception:
        logger.exception("ps_008: failed to apply the MCP tool attribution wrapper.")
    try:
        _wrap_handle_django_request()
    except Exception:
        logger.exception("ps_008: failed to wrap DjangoMCP.handle_django_request.")


# --------------------------------------------------------------------------- #
# Hook b: history rows
# --------------------------------------------------------------------------- #
def _stamp_history(history):
    message = history.event_message or ""
    if MARKER_PREFIX in message:
        return
    attribution = current_attribution() or _inherited_for_history(history)
    if attribution is not None:
        history.event_message = f"{message} {attribution.marker()}".strip()


def patch_mcp_attribution_b_history():
    try:
        from history.models import HistoryModel

        original = HistoryModel.save
        if getattr(original, "_ps008_patched", False):
            logger.info("ps_008: history hook already applied; skipping.")
            return

        @functools.wraps(original)
        def save(self, *args, **kwargs):
            if getattr(getattr(self, "_state", None), "adding", False):
                try:
                    _stamp_history(self)
                except Exception:
                    logger.debug("ps_008: history stamp failed", exc_info=True)
            return original(self, *args, **kwargs)

        save._ps008_patched = True
        HistoryModel.save = save
        logger.info("ps_008: applied history attribution hook.")
    except Exception:
        logger.exception("ps_008: failed to apply the history attribution hook.")


# --------------------------------------------------------------------------- #
# Hook c: jobs
# --------------------------------------------------------------------------- #
def _on_job_created(sender, instance, created, **kwargs):
    if not created:
        return
    try:
        attribution = current_attribution() or _inherited_for_new_job(instance)
        if attribution is None:
            return
        from jobs.models import ProgressMessage

        ProgressMessage.objects.create(
            job=instance, message=attribution.progress_message()
        )
        _bounded_put(_job_cache, instance.id, attribution)
    except Exception:
        logger.debug("ps_008: job stamp failed", exc_info=True)


def patch_mcp_attribution_c_jobs():
    try:
        from django.db.models.signals import post_save
        from jobs.models import Job

        post_save.connect(
            _on_job_created,
            sender=Job,
            weak=False,
            dispatch_uid="ps_008_mcp_job_attribution",
        )
        logger.info("ps_008: applied job attribution hook.")
    except Exception:
        logger.exception("ps_008: failed to apply the job attribution hook.")


# --------------------------------------------------------------------------- #
# Hook d: internal API hop (approve / cancel / deny order)
# --------------------------------------------------------------------------- #
def _wrap_internal_api_client():
    from api.v3.internal_api_client import InternalAPIClient

    original = InternalAPIClient.__init__
    if getattr(original, "_ps008_patched", False):
        return

    @functools.wraps(original)
    def __init__(self, user, *args, **kwargs):
        original(self, user, *args, **kwargs)
        attribution = current_attribution()
        if attribution is None:
            return
        try:
            from rest_framework_simplejwt.tokens import RefreshToken

            access = RefreshToken.for_user(user).access_token
            access[CLAIM_AGENT] = attribution.agent
            access[CLAIM_CLIENT] = attribution.client
            access[CLAIM_SESSION] = attribution.session
            self.token = str(access)
            self.http_session.headers["Authorization"] = f"Bearer {self.token}"
        except Exception:
            logger.debug("ps_008: could not add attribution claims", exc_info=True)

    __init__._ps008_patched = True
    InternalAPIClient.__init__ = __init__


def _wrap_jwt_authentication():
    from rest_framework_simplejwt.authentication import JWTAuthentication

    original = JWTAuthentication.authenticate
    if getattr(original, "_ps008_patched", False):
        return

    @functools.wraps(original)
    def authenticate(self, request):
        result = original(self, request)
        try:
            if result is not None:
                _user, token = result
                get = getattr(token, "get", None)
                if callable(get) and get(CLAIM_AGENT):
                    _current.set(
                        Attribution(
                            agent=str(get(CLAIM_AGENT))[:AGENT_MAX_LEN],
                            client=str(get(CLAIM_CLIENT) or ""),
                            session=str(get(CLAIM_SESSION) or ""),
                            jti=str(get("jti") or ""),
                        )
                    )
        except Exception:
            logger.debug("ps_008: could not read attribution claims", exc_info=True)
        return result

    authenticate._ps008_patched = True
    JWTAuthentication.authenticate = authenticate


def _clear_attribution(**kwargs):
    # A plain WSGI thread keeps its context between requests; make sure
    # claim-derived attribution never outlives the request that carried it.
    _current.set(None)


def patch_mcp_attribution_d_internal_api():
    try:
        _wrap_internal_api_client()
    except Exception:
        logger.exception("ps_008: failed to wrap InternalAPIClient.")
    try:
        _wrap_jwt_authentication()
        from django.core.signals import request_finished, request_started

        request_started.connect(
            _clear_attribution, weak=False, dispatch_uid="ps_008_clear_on_start"
        )
        request_finished.connect(
            _clear_attribution, weak=False, dispatch_uid="ps_008_clear_on_finish"
        )
        logger.info("ps_008: applied internal API attribution hook.")
    except Exception:
        logger.exception("ps_008: failed to wrap JWTAuthentication.")
