from common.methods import set_progress
import json
from onefuse.cloudbolt_admin import CbOneFuseManager, Utilities
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def run(job, *args, **kwargs):
    resource = job.get_resource()
    if resource:
        utilities = Utilities(logger)
        set_progress("This plug-in is running for resource " + resource.name)
        naming_json = resource.get_cfv_for_custom_field("OneFuse_Naming")
        logger.debug(f'naming_json: {naming_json}')
        if naming_json:
            naming_json = naming_json.value_as_string
            naming_obj = json.loads(naming_json)
            onefuse_endpoint = naming_obj["endpoint"]
            naming_policy_name = naming_obj["name"]
            name_id = naming_obj["id"]
            if onefuse_endpoint and naming_policy_name and name_id:
                set_progress(f"Starting OneFuse Delete Name Object. Policy: "
                             f"{naming_policy_name}, Endpoint: "
                             f"{onefuse_endpoint}, Name ID: {name_id}")

                #Delete Name Object
                from xui.onefuse.globals import VERIFY_CERTS
                ofm = CbOneFuseManager(onefuse_endpoint, VERIFY_CERTS,
                                       logger=logger)
                deleted_name = ofm.deprovision_naming(name_id)
                return_string = f"Name was successfully deleted from the "\
                                f"OneFuse database. Name: {deleted_name}"
                return "SUCCESS", return_string, ""
            else:
                set_progress(f"OneFuse Naming policy, endpoint of Name ID "
                             f"was missing, Execution skipped")
                return_string = f"No OneFuse Name ID identified. "\
                                f"Name deletion skipped."
                return "SUCCESS", return_string, ""
        else:
            set_progress(f"OneFuse Naming policy, endpoint of Name ID was "
                         f"missing, Execution skipped")
            return_string = "No OneFuse Name ID identified. Name deletion "\
                            f"skipped."
            return "SUCCESS", return_string, ""
    else:
        set_progress("Resource was not defined for Job, exiting")