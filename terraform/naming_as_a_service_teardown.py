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
        naming_infos = get_onefuse_naming_infos(resource)
        for naming_info in naming_infos:
            onefuse_endpoint, name_id = naming_info
            set_progress(f"Starting OneFuse Delete Name Object. Endpoint: "
                         f"{onefuse_endpoint}, Name ID: {name_id}")

            #Delete Name Object
            from xui.onefuse.globals import VERIFY_CERTS
            ofm = CbOneFuseManager(onefuse_endpoint, VERIFY_CERTS,
                                   logger=logger)
            try:
                deleted_name = ofm.deprovision_naming(name_id)
                set_progress(f"Name was successfully deleted from the "
                             f"OneFuse database. Name: {deleted_name}")
            except Exception as e:
                logger.info(f'OneFuse Name could not be found, assuming '
                            f'deleted')

        return "SUCCESS", "", ""
    else:
        set_progress("Resource was not defined for Job, exiting")


def get_onefuse_naming_infos(resource):
    cfvs = resource.get_cf_values_as_dict()
    prefix = "OneFuse_Naming_"
    naming_infos = []
    for key in cfvs.keys():
        if key.find(prefix) == 0:
            naming_json = cfvs[key]
            naming_obj = json.loads(naming_json)
            onefuse_endpoint = naming_obj["endpoint"]
            name_id = naming_obj["id"]
            naming_infos.append((onefuse_endpoint, name_id))
    return naming_infos
