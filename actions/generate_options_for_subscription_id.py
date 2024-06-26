"""
Presents a form that appears to create an Azure account
"""
from django.db.models import Q

from common.methods import set_progress
from infrastructure.models import Environment
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def get_options_list(field, **kwargs):
    """
    Generate a list of Azure environments available to the group submitting
    the order
    """
    set_progress(f'kwargs: {kwargs}')
    group = kwargs.get("group")
    set_progress("Group: {}".format(group))
    if not group:
        return None

    envs = Environment.objects.filter(
        (Q(group__in=[group]) | Q(group=None)) &
        Q(resource_handler__resource_technology__name="Azure")
    )

    rhs = []
    envs = [env for env in envs if env.is_unassigned == False]
    for env in envs:
        rh = env.resource_handler.cast()
        rh_option = (rh.serviceaccount, rh.name)
        if rh_option not in rhs:
            rhs.append(rh_option)

    if not rhs:
        rhs = [("", "------No Subscriptions Available------")]
    else:
        rhs.insert(0, ("", "------ Select a Subscription ------"))
    return rhs
