from common.methods import set_progress
from infrastructure.models import CustomField
from utilities.logger import ThreadLogger
import json

logger = ThreadLogger(__name__)


def run(job, **kwargs):
    server = job.server_set.first()
    if server:
        tags_string = server.tags_string
        create_tags(tags_string, server)

    return "SUCCESS", "", ""


def create_tags(tags_string, server):
    tags_json = json.loads(tags_string)
    for key in tags_json.keys():
        param_name = f'tags_{key}'
        param_value = tags_json[key]
        defaults = {
            "show_on_servers": True,
            "label": key
        }
        if param_value:
            cf, _ = CustomField.objects.get_or_create(
                name=param_name, type="STR", defaults=defaults
            )
            server.set_value_for_custom_field(param_name, param_value)
