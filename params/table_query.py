"""
Get Options Action for App ID
"""
from common.methods import set_progress
import json


def get_options_list(field, **kwargs):
    options = [('', '--- Select an App ID ---')]
    file_path = '/var/opt/cloudbolt/proserv/service_now/app_ids.json'
    with open(file_path, 'r') as f:
        content = f.read()
        results = json.loads(content)
        for result in results:
            options.append((json.dumps(result), result["u_application_id"]))

    return options
