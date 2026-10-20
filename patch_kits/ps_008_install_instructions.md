# Installing the CloudBolt Patch Kit and the MCP agent-attribution patch (ps_008)

Run these as root on the CloudBolt server.

## 1. Download and extract the patch kit (skip if `/var/opt/cloudbolt/proserv/patch_kit/` already exists)

```bash
cd /var/opt/cloudbolt/proserv
curl -fsSL -o patch_kit.tar.gz https://raw.githubusercontent.com/mbomb67/cloudbolt_samples/main/patch_kits/patch_kit.tar.gz
tar xzf patch_kit.tar.gz && rm patch_kit.tar.gz
```

This creates `/var/opt/cloudbolt/proserv/patch_kit/` with an empty `patch_sets/` folder.

## 2. Download the patch into that folder

```bash
curl -fsSL -o /var/opt/cloudbolt/proserv/patch_kit/patch_sets/ps_008_mcp_agent_attribution.py https://raw.githubusercontent.com/mbomb67/cloudbolt_samples/main/patch_kits/patch_sets/ps_008_mcp_agent_attribution.py
```

## 3. Set ownership to cloudbolt:cloudbolt

```bash
chown -R cloudbolt:cloudbolt /var/opt/cloudbolt/proserv/patch_kit
```

Confirm with `ls -l /var/opt/cloudbolt/proserv/patch_kit/patch_sets/`. The ps_008 file must show `cloudbolt cloudbolt`.

## 4. Register the patch kit (skip if already present)

Add these lines to `/var/opt/cloudbolt/proserv/customer_settings.py` (create the file if it does not exist):

```python
from settings import INSTALLED_APPS, PROSERV_DIR

INSTALLED_APPS += ('patch_kit.apps.PatchKitConfig',)
```

## 5. Restart the web service AND the job engine

Unlike earlier patch sets, this one must load in both processes. The job engine needs it so child jobs and provisioning-time history inherit the attribution of the MCP call that started them.

```bash
systemctl restart httpd
```

```bash
systemctl restart jobengine
```

## 6. Verify

1. Connect an MCP client (Claude Desktop, MCP Inspector) through the normal OAuth flow. Note the client's name under **Admin > OAuth Applications**.
2. From the agent, run one write tool: for example `order_catalog_item` on a trivial blueprint, or `run_server_action` on a test server.
3. Check the request log. Each tool call now carries `agent=`, `client=`, `session=` and `jti=`:

```bash
grep 'agent="' /var/log/cloudbolt/mcp.log | tail -5
```

4. Check the records in a Django shell:

```bash
/opt/cloudbolt/manage.py shell_plus -c "from history.models import OrderHistory, ServerHistory; from jobs.models import ProgressMessage; print(OrderHistory.objects.filter(event_message__contains='[mcp agent=').order_by('-id')[:5].values_list('order_id','event_message')); print(ProgressMessage.objects.filter(message__startswith='[mcp agent=').order_by('-id')[:5].values_list('job_id','message')); print(ServerHistory.objects.filter(event_message__contains='[mcp agent=').order_by('-id')[:5].values_list('server_id','event_message'))"
```

   The Order's **History** tab, the Job's progress list and the Server's **History** tab show the same marker in the UI.

5. Negative checks: an order placed in the UI and a direct `/api/v3/` call show no marker. A request with a bad token still gets the normal 401.

## What the patch does

Every audit record written during an MCP tool call is stamped with the OAuth client that acted (for example `Claude`) and a 12-character session id for that agent connection, in the form:

```
[mcp agent="Claude" client="<client_id>" session="4f3a9c2e1b7d"]
```

The marker is appended to Order, Server and Resource history messages, written as the first progress message of each Job (including child jobs started by the job engine), and added to each line in `/var/log/cloudbolt/mcp.log`. Orders approved, cancelled or denied by an agent are attributed as well, through signed claims on the internal API token.

The patch is applied when CloudBolt starts. To remove it, delete the ps_008 file and restart `httpd` and `jobengine`. Markers already written stay in those records.
