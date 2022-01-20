from common.methods import set_progress
import json
from onefuse.cloudbolt_admin import CbOneFuseManager, Utilities
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, *args, **kwargs):
    resource = job.get_resource()
    if resource:
        utilities = Utilities(logger)
        set_progress("Deleting OneFuse Objects for resource " + resource.name)
        teardown_naming(resource)
        teardown_ipam(resource)
        teardown_dns(resource)
        teardown_ad(resource)
    else:
        set_progress("Resource was not defined for Job, exiting")


def teardown_naming(resource):
    job_string = resource.get_cfv_for_custom_field("OneFuse_Naming").value
    logger.debug(f'Naming job_string: {job_string}')
    if job_string:
        delete_object_from_job_string(job_string)
    else:
        set_progress(f"Job string missing, delete name skipped")


def teardown_ipam(resource):
    job_string = resource.get_cfv_for_custom_field("OneFuse_Ipam_Nic0").value
    logger.debug(f'IPAM job_string: {job_string}')
    if job_string:
        delete_object_from_job_string(job_string)
    else:
        set_progress(f"Job string missing, delete IPAM skipped")


def teardown_dns(resource):
    job_string = resource.get_cfv_for_custom_field("OneFuse_AD").value
    logger.debug(f'AD job_string: {job_string}')
    if job_string:
        delete_object_from_job_string(job_string)
    else:
        set_progress(f"Job string missing, delete AD skipped")


def teardown_ad(resource):
    job_string = resource.get_cfv_for_custom_field("OneFuse_Dns_Nic0").value
    logger.debug(f'AD job_string: {job_string}')
    if job_string:
        delete_object_from_job_string(job_string)
    else:
        set_progress(f"Job string missing, delete AD skipped")


def delete_object_from_job_string(job_string):
    job_obj = json.loads(job_string)
    endpoint = job_obj["endpoint"]
    policy_name = job_obj["_links"]["policy"]["title"]
    policy_type = job_obj["_links"]["policy"]["href"].split("/")[4]
    if policy_type == "ipamPolicies":
        name = job_obj["hostname"]
    else:
        name = job_obj["name"]
    href = job_obj["_links"]["self"]["href"]
    delete_href = f'/{"/".join(href.split("/")[4:])}'
    if endpoint and policy_name and delete_href:
        set_progress(f"Starting OneFuse Delete {policy_type} Object. Policy: "
                     f"{policy_name}, Name: {name}, Endpoint: {endpoint}, "
                     f"href: {delete_href}")
        from xui.onefuse.globals import VERIFY_CERTS
        ofm = CbOneFuseManager(endpoint, VERIFY_CERTS, logger=logger)
        ofm.deprovision_mo(delete_href)
    else:
        set_progress(f"Policy, endpoint or ID missing, delete {policy_type} "
                     f"skipped")
