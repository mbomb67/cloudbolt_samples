import json
from urllib.parse import urlencode

from django.db.models import Q
from infrastructure.models import Server
from jobs.models import Job
from itsm.models import ITSM


REFERENCE_FIELDS = {
    "manufacturer": ("core_company", "name"),
    "company": ("core_company", "name"),
}

# The IRE attributes the payload to this data source (sysparm_data_source),
# which lets the customer's reconciliation rules grant CloudBolt authority
# over the attributes it owns. Taken from the CMDB field map's discovery_source
# value; the value must be a discovery_source choice on cmdb_ci.
# https://www.servicenow.com/docs/r/eJIqiaxRKVcMaR8XQU6_oA/AawFLNaPF5h3FoI97~DLtg
DATA_SOURCE_KEY = "discovery_source"


def _resolve_reference_fields(service_now_api, cmdb_items, job):
    cache = {}
    for item in cmdb_items:
        values = item["values"]
        for field, (table, display_col) in REFERENCE_FIELDS.items():
            raw = values.get(field)
            if not raw or len(raw) == 32:  # empty or already a sys_id
                continue
            key = (table, raw)
            if key not in cache:
                query = urlencode(
                    {
                        "sysparm_query": f"{display_col}={raw}",
                        "sysparm_fields": "sys_id",
                        "sysparm_limit": 1,
                    }
                )
                url = f"{service_now_api.service_now_instance_url}/api/now/table/{table}?{query}"
                results = service_now_api._snow_get(url).json().get("result", [])
                cache[key] = results[0]["sys_id"] if results else None
            if cache[key]:
                values[field] = cache[key]
            else:
                job.set_progress(f"No {table} record named '{raw}'; leaving {field} unset.")
                values.pop(field)  # no match: drop rather than corrupt the column


def _identify_reconcile(service_now_api, cmdb_items, relations, data_source, job):
    """
    POST /api/now/identifyreconcile with the payload attributed to data_source.
    Returns True when the IRE accepted every item; masked attributes are logged
    because the IRE reports them as success.
    """
    url = f"{service_now_api.service_now_instance_url}/api/now/identifyreconcile"
    if data_source:
        url += "?" + urlencode({"sysparm_data_source": data_source})
    payload = {"items": cmdb_items}
    if relations:
        payload["relations"] = relations
    job.set_progress(f"ServiceNow IRE data source: {data_source or 'instance default'}")

    response = service_now_api._snow_post(url, body=json.dumps(payload))
    job.set_progress(f"IRE response: {response.text[:2000]}")
    if response.status_code not in (200, 201):
        return False

    ok = True
    for item in response.json().get("result", {}).get("items", []):
        masked = item.get("maskedAttributes") or []
        if masked:
            job.set_progress(
                f"IRE {item.get('operation')} {item.get('sysId')}: reconciliation rules "
                f"masked {', '.join(masked)} for source '{data_source or 'instance default'}'."
            )
        if item.get("errors"):
            job.set_progress(f"IRE errors: {item['errors']}")
            ok = False
    return ok


def create_or_update_servicenow_cmdb_record(server: Server, job: Job, *args, **kwargs):
    """
    Create a ServiceNow CMDB Record. Unique server resource ID will be sent to ServiceNow.
    Utilize CloudBolt ServiceNowWrapper wrapper to make request and process response.
    :param server: The Server instance to create or update a CI record for.
    :param job: The Job which this will run in.
    :param kwargs['cmdb_id'] The CMDB id which can be used to customize payload sent to ServiceNow.
    """

    try:
        itsm_id = kwargs.pop("itsm_id")
    except KeyError:
        error = "CMDB ID not found. The ServiceNow cmdb hook is enabled but no CMDB data set was found. Completing job with a success."
        return "SUCCESS", "", error

    """
    Get the ITSM
    """
    try:
        itsm = ITSM.objects.get(pk=itsm_id)
    except Exception as error:
        return "FAILURE", "", error

    cmdbs = itsm.get_cmdbs_for_server(server)
    cmdb_items = []

    for cmdb in cmdbs:
        context = cmdb.get_context(server=server, job=job)
        cmdb_data = cmdb.get_context_data(context)

        cmdb_items.append(
            {
                "cmdb_id": cmdb.id,
                "className": cmdb.table_name,
                "values": cmdb_data,
            }
        )

    if not cmdb_items:
        job.set_progress(
            f"No CMDB configuration matches {server.hostname} (environment, OS build, "
            f"resource technology); nothing sent to ServiceNow."
        )
        return "SUCCESS", "", ""

    # Get Relations
    cmdb_list_index_map = {}
    cmdb_ids = []

    for i in range(len(cmdb_items)):
        cmdb_id = cmdb_items[i].pop("cmdb_id")
        cmdb_list_index_map[cmdb_id] = i
        cmdb_ids.append(cmdb_id)

    relation_ids = []
    for cmdb_id in cmdb_ids:
        cmdb_relation_ids = itsm.cmdb_relationships.filter(
            Q(parent_cmdb_id=cmdb_id, child_cmdb_id__in=cmdb_ids)
            | Q(parent_cmdb_id__in=cmdb_ids, child_cmdb_id=cmdb_id)
        ).values_list("id", flat=True)

        relation_ids.extend(cmdb_relation_ids)

    relations = []
    for relation in itsm.cmdb_relationships.filter(id__in=relation_ids):
        relations.append(
            {
                "type": f"{relation.parent_relation_type}::{relation.child_relation_type}",
                "parent": cmdb_list_index_map.get(relation.parent_cmdb.id),
                "child": cmdb_list_index_map.get(relation.child_cmdb.id),
            }
        )

    # Get the appropriate wrapper for this technology.
    service_now_api = itsm.get_api_wrapper()

    _resolve_reference_fields(service_now_api, cmdb_items, job)

    # The first rendered discovery_source names the IRE data source for the
    # whole payload; the key stays in the values as an attribute as well.
    data_source = next(
        (item["values"].get(DATA_SOURCE_KEY) for item in cmdb_items if item["values"].get(DATA_SOURCE_KEY)),
        None,
    )

    # Create or update the CI's (Configuration Item)
    job.set_progress(f"ServiceNow IRE payload: items={cmdb_items}, relations={relations}")

    try:
        result = _identify_reconcile(service_now_api, cmdb_items, relations, data_source, job)
    except RuntimeError as error:
        return "FAILURE", "", error

    if not result:
        job.set_progress(
            "Unable to create or update ServiceNow CI entries for {}.".format(
                server.hostname
            )
        )
        return "FAILURE", "", ""

    job.set_progress(f"Updated ServiceNow CMDB for {server.hostname}")
    return "SUCCESS", "", ""