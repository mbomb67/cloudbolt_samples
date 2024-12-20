"""
Runs a script against the controller node to get the join command for the
Kubernetes cluster.
"""
from c2_wrapper import create_custom_field
from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, resource=None, *args, **kwargs):
    logger.debug(f'kwargs: {kwargs}')
    join_script = get_script()
    server = resource.server_set.get(service_item__name="Controller")
    set_progress("Executing script on controller node to get the join command.")
    result = server.execute_script(script_contents=join_script)
    return "SUCCESS", result, ""


def get_script():
    return """#!/bin/bash
kubectl get nodes
"""
