"""
Used in a Recurring Job to report back the number of Servers per Each OS in
CloudBolt
"""
import json
from common.methods import set_progress
from externalcontent.models import OSFamily


def run(job=None, logger=None, **kwargs):
    set_progress('Running ServiceNow Request queue manager')
    os_families = OSFamily.objects.all()
    results = {}
    for os_family in os_families:
        family_name = os_family.name
        server_count = len(os_family.server_set.filter(status='ACTIVE'))
        results[family_name] = server_count
    set_progress(f'results: {json.dumps(results)}')
    return "SUCCESS", json.dumps(results), ""
