"""
Adds a "Jobs" tab to the MCP Tool Action detail page (Admin > MCP Tool
Actions > <action>), listing the Jobs spawned by running the action.

Proof-of-concept for extending the action-wrapper detail page, which today
builds a hardcoded tab list in ``cbhooks.views._get_action_detail_tabs`` and
consults neither the XUI tab-extension system (MCPToolAction is not in
``TabExtensionManager.supported_models``) nor ``ExtensionManager.merge_tabs``.

How it works:
    ``cbhooks.views.action_detail_by_type`` resolves
    ``_get_action_detail_tabs`` through module globals at call time, so
    rebinding the module attribute here takes effect for every request. The
    wrapper calls the original, and for ``url_prefix == "mcp_tool_action"``
    appends a custom Tab whose ``render()`` builds the table directly (no
    template file needed on disk).

Which jobs are shown:
    An async MCPToolAction run goes through ``RunHookMixin.run_hook_as_job``
    -> ``CloudBoltHook.run_as_job``, which creates
    ``Job(type="orchestration_hook", job_parameters=HookParameters(hook=...))``.
    So we list Jobs whose HookParameters point at the action's underlying
    hook.

Caveats (inherent to the data model, not this patch):
  * Synchronous MCP Tool Actions run inline and create NO Job, so those
    invocations never appear here.
  * Jobs are keyed to the underlying CloudBoltHook. If the same plugin is
    also wired to other triggers (server action, recurring job, ...), those
    runs appear in this list too.

Deploy: copy to PROSERV_DIR/patch_kit/patch_sets/ with the patch_kit app in
INSTALLED_APPS (customer_settings.py), then restart httpd. The job engine is
not involved -- this only patches a web view.
"""

from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

MAX_JOBS_SHOWN = 100

_STATUS_BADGES = {
    "SUCCESS": "bg-success",
    "FAILURE": "bg-danger",
    "WARNING": "bg-warning text-dark",
    "RUNNING": "bg-info text-dark",
    "ACTIVE": "bg-info text-dark",
    "CANCELED": "bg-secondary",
    "PENDING": "bg-secondary",
    "QUEUED": "bg-secondary",
    "INIT": "bg-secondary",
}


def _fmt_date(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else "—"


def patch_mcp_tool_action_jobs_tab():
    import cbhooks.views as cbhooks_views
    from jobs.models import Job
    from tabs.views import Tab

    if getattr(cbhooks_views._get_action_detail_tabs, "_mcp_jobs_tab_patch", False):
        logger.info("MCP Tool Action Jobs tab patch already applied; skipping.")
        return

    original_get_tabs = cbhooks_views._get_action_detail_tabs

    class MCPToolActionJobsTab(Tab):
        """Self-rendering tab: overrides render() so no template file is needed."""

        def __init__(self, hook, request):
            super().__init__("Jobs", "mcp-jobs", template=None, request=request)
            self.hook = hook

        def render(self):
            jobs = (
                Job.objects.filter(job_parameters__hookparameters__hook=self.hook)
                .select_related("owner__user")
                .order_by("-id")[:MAX_JOBS_SHOWN]
            )

            if not jobs:
                return mark_safe(
                    '<p class="text-muted p-3">No jobs have been run for this'
                    " MCP Tool Action yet. (Synchronous runs execute inline and"
                    " do not create jobs.)</p>"
                )

            rows = format_html_join(
                "\n",
                "<tr>"
                '<td><a href="{}">Job {}</a></td>'
                '<td><span class="badge {}">{}</span></td>'
                "<td>{}</td><td>{}</td><td>{}</td>"
                "</tr>",
                (
                    (
                        job.get_absolute_url(),
                        job.id,
                        _STATUS_BADGES.get(job.status, "bg-secondary"),
                        job.get_status_display(),
                        str(job.owner) if job.owner else "—",
                        _fmt_date(job.start_date),
                        _fmt_date(job.end_date),
                    )
                    for job in jobs
                ),
            )

            return format_html(
                '<table class="table table-hover align-middle">'
                "<thead><tr>"
                "<th>Job</th><th>Status</th><th>Owner</th>"
                "<th>Started</th><th>Ended</th>"
                "</tr></thead><tbody>{}</tbody></table>",
                rows,
            )

    def _get_action_detail_tabs(request, profile, action, hook, url_prefix):
        tabs = original_get_tabs(request, profile, action, hook, url_prefix)
        if url_prefix == "mcp_tool_action":
            tabs.append(MCPToolActionJobsTab(hook, request))
        return tabs

    _get_action_detail_tabs._mcp_jobs_tab_patch = True
    cbhooks_views._get_action_detail_tabs = _get_action_detail_tabs
    logger.info("Applied MCP Tool Action Jobs tab patch.")
