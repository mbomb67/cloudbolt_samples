import datetime

"""
CloudBolt Plug-in hook used as a sample to automatically generate options for
the expiration date parameter based on offsets from the current day
"""


def get_options_list(field, environment=None, group=None, **kwargs):

    return {
        "initial_value": datetime.datetime.now() + datetime.timedelta(days=1)
    }