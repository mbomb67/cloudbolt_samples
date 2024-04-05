from datetime import time

from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, *args, **kwargs):
    # I'm not replicating your inputs to capture the numbers via file upload,
    # just creating a generic list of different numbers:
    change_numbers = [4105551234, 4105551235, 4105551236, 4105551237]
    request_ids = submit_lumen_requests(change_numbers)
    wait_for_request_completions(request_ids)
    return "SUCCESS", "All requests have been completed", ""


def submit_lumen_requests(change_numbers):
    request_ids = []
    for number in change_numbers:
        # This is where you would submit the change request to Lumen
        # and get back the request ID
        request_id = f"REQ-{number}"
        request_ids.append(request_id)
    return request_ids


def wait_for_request_completions(request_ids):
    total_requests = len(request_ids)
    completed_requests = []
    failed_requests = []
    # Use a sleep interval to avoid hammering the API - total number of seconds
    # the job has been sleeping
    total_sleep = 0
    # Configure a sleep interval - this is in seconds
    sleep_time = 60
    # Configure a timeout in case the requests never complete
    max_sleep = 3600
    while request_ids:
        for request_id in request_ids:
            # This is where you would check the status of the request
            # and wait until it is complete
            response = {"status": "COMPLETE"}  # or "PENDING" or "FAILED
            if response.status == "COMPLETE":
                logger.info(f"Request {request_id} is complete")
                completed_requests.append(request_id)
            elif response.status == "FAILED":
                logger.error(f"Request {request_id} failed")
                failed_requests.append(request_id)
        set_progress(f"Completed {len(completed_requests)} of "
                     f"{total_requests} requests")
        request_ids = [request_id for request_id in request_ids if request_id
                       not in completed_requests and request_id not in
                       failed_requests]
        total_sleep += sleep_time
        if total_sleep > max_sleep:
            raise Exception("Requests did not complete within the timeout")
        time.sleep(sleep_time)
    return
