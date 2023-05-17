"""
This module is used to store the methods for setting up the NSX-T XUI
"""
import json
import os
from os import path
from packaging import version

from cbhooks.models import CloudBoltHook
from servicecatalog.models import ServiceBlueprint
from resourcehandlers.models import ResourceHandler
from xui.nsxt.xui_utilities import check_for_nsxt, setup_nsx_tags
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

XUI_PATH = path.dirname(path.abspath(__file__))
XUI_NAME = XUI_PATH.split("/")[-1]
CONFIG_FILE = f'/var/opt/cloudbolt/proserv/xui/xui_versions.json'


def get_data_from_config_file(property_key):
    with open(CONFIG_FILE, 'r') as f:
        config = json.load(f)
        data = config[XUI_NAME][property_key]
    return data


# If we find a Blueprint with the same name, should it be overwritten?
try:
    OVERWRITE_EXISTING_BLUEPRINTS = get_data_from_config_file(
        'OVERWRITE_EXISTING_BLUEPRINTS')
except Exception:
    OVERWRITE_EXISTING_BLUEPRINTS = False
# From what I can tell, when a Blueprint is using a remote source, the actions
# are only updated at initial creation. Setting this toggle to True would
# set each action to use the remote source - forcing update of the actions when
# the XUI gets updated
try:
    SET_ACTIONS_TO_REMOTE_SOURCE = get_data_from_config_file(
        'SET_ACTIONS_TO_REMOTE_SOURCE')
except Exception:
    SET_ACTIONS_TO_REMOTE_SOURCE = False


def run_config(xui_version):
    config_needed = False
    try:
        with open(CONFIG_FILE, 'r') as f:
            config = json.load(f)
            current_version = config[XUI_NAME]["current_version"]
            if version.parse(current_version) < version.parse(xui_version):
                logger.info(f"Current Version: {current_version} is less than"
                            f" {xui_version}. Running config.")
                config_needed = True
    except FileNotFoundError:
        logger.info(f"Config file not found going to run configuration")
        config_needed = True
    if config_needed:
        logger.info("Running Configuration")
        configure_xui()
        try:
            config
        except NameError:
            config = {}
        config[XUI_NAME] = {
            "current_version": xui_version,
            "SET_ACTIONS_TO_REMOTE_SOURCE": SET_ACTIONS_TO_REMOTE_SOURCE,
            "OVERWRITE_EXISTING_BLUEPRINTS": OVERWRITE_EXISTING_BLUEPRINTS
        }
        with open(CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)


def configure_xui():
    configure_tags()
    configure_blueprints()


def configure_blueprints():
    blueprints_dir = f'{XUI_PATH}/blueprints/'
    for bp in os.listdir(blueprints_dir):
        bp_dir = f'{blueprints_dir}{bp}/'
        bp_path = f'{bp_dir}{bp}.json'
        with open(bp_path, 'r') as f:
            bp_json = json.load(f)
        bp_name = bp_json["name"]
        bp, created = ServiceBlueprint.objects.get_or_create(name=bp_name,
                                                             status='ACTIVE')
        if not created:
            if OVERWRITE_EXISTING_BLUEPRINTS:
                logger.info(f"Overwriting Blueprint: {bp_name}")
            else:
                logger.info(f"Blueprint: {bp_name} already exists. Skipping")
                continue
        bp.remote_source_url = f'file://{bp_path}'
        bp.save()
        bp.refresh_from_remote_source()
        logger.info(f"Finished refreshing: {bp_name} from remote source")
        set_actions_to_remote_source(bp_dir, bp_json, created)


def set_actions_to_remote_source(bp_dir, bp_json, created):
    if SET_ACTIONS_TO_REMOTE_SOURCE or created:
        logger.info(f'Starting to set actions to remote source for BP: '
                    f'{bp_json["name"]}')
        action_datas = []  # Tuples of (action_name, action_path)
        elements = ["teardown-items", "build-items", "management-actions"]
        for element in elements:
            for action in bp_json[element]:
                action_data = get_action_data(action, bp_dir, element)
                action_datas.append(action_data)
        for action_data in action_datas:
            action_name, action_path = action_data
            logger.info(f"Setting action: {action_name} to remote source")
            set_action_to_remote_source(action_name, action_path)
    else:
        logger.info("Not setting actions to remote source. Update the "
                    "SET_ACTIONS_TO_REMOTE_SOURCE variable to True if you "
                    "want to do this")
    return None


def set_action_to_remote_source(action_name, action_path):
    try:
        action = CloudBoltHook.objects.get(name=action_name)
        action.source_code_url = f'file://{action_path}'
        action.save()
    except:
        logger.warning(f"Could not find action: {action_name}, will not be "
                       f"able to set to remote source")


def get_action_data(action, bp_dir, item_name):
    if item_name == 'management-actions':
        action_name = action["label"].replace(" ", "_").replace("-",
                                                                "_").lower()
        json_file = f'{action_name}.json'
        json_path = f'{bp_dir}{action_name}/{action_name}/{json_file}'
    else:
        action_name = action["name"].replace(" ", "_").replace("-",
                                                               "_").lower()
        json_file = f'{action_name}.json'
        json_path = f'{bp_dir}{action_name}/{action_name}.json'
    action_path = get_action_path_from_json(json_path, json_file)
    return action_name, action_path


def get_action_path_from_json(json_path, json_file):
    with open(json_path, 'r') as f:
        action_json = json.load(f)
    action_file = action_json["script-filename"]
    action_path = json_path.replace(json_file, action_file)
    return action_path


def configure_tags():
    # Create the nsxt_tag parameter if it does not already exist
    cf, _ = setup_nsx_tags()

    # Add an NSXT parameter to any NSXT environments
    rhs = ResourceHandler.objects.all()
    for rh in rhs:
        if check_for_nsxt(rh):
            for env in rh.environment_set.all():
                env.custom_fields.add(cf)
