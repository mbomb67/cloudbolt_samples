"""
Take an input CloudBolt Parameter, attempt to load it with JSON and YAML -
allowing for the input of either JSON or YAML - then format and save the
parameter value as a string formatted in JSON.
"""

import yaml
import json
from json import JSONDecodeError

YAML_PARAM_NAME = "yaml_param"
OUTPUT_PARAM_NAME = "output_param"

def run(job, **kwargs):
    server = job.server_set.first()
    if server:
        try:
            input_value = server.get_cfv_for_custom_field(YAML_PARAM_NAME).value
        except AttributeError:
            msg = f"Custom Field not found on machine: {YAML_PARAM_NAME}"
            return "SUCCESS", msg, ""
        # Try JSON load first, if fails try YAML Loads
        try:
            input_object = json.loads(input_value)
        except JSONDecodeError:
            # Try Yaml
            try:
                input_object = yaml.safe_load(input_value)
            except Exception as e:
                msg = f'Failed to load the input_object. Error: {e}'
                return "FAILED", msg, ""
        output_string = json.dumps(input_object)
        server.set_value_for_custom_field(OUTPUT_PARAM_NAME, output_string)
        return "SUCCESS", "", ""
