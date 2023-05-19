"""
Based on a boolean parameter - add one set of applications if true, another
set if false
"""

from common.methods import set_progress
from externalcontent.models import Application
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def generate_options_for_apps_if_true(field=None, **kwargs):
    applications = Application.objects.all()
    return [(app.id, app.name) for app in applications]


def generate_options_for_apps_if_false(field=None, **kwargs):
    applications = Application.objects.all()
    return [(app.id, app.name) for app in applications]


def run(job, server=None, **kwargs):
    parameter_name = "{{parameter_name}}" # Name of the boolean param
    apps_if_true = list({{apps_if_true}})
    apps_if_false = list({{apps_if_false}})
    logger.info(f'parameter_name: {parameter_name}, apps_if_true: '
                f'{apps_if_true}, apps_if_false: {apps_if_false}')
    parameter_value = server.get_value_for_custom_field(parameter_name)
    if parameter_value is None:
        logger.info(f'Parameter: {parameter_name} is not set. Exiting.')
        return "", "", ""
    if parameter_name:
        apps = apps_if_true
    else:
        apps = apps_if_false
    for app in apps:
        application = Application.objects.get(id=app)
        set_progress(f'Adding application: {application.name} to server '
                     f'{server.hostname}')
        server.applications.add(application)

    return "SUCCESS", "", ""