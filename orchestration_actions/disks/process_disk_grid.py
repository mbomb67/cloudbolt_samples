from ast import literal_eval

from c2_wrapper import create_custom_field
from common.methods import set_progress
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)



def run(job=None, server=None, **kwargs):
    disk_grid = server.disk_grid
    disk_list = literal_eval(disk_grid)
    set_progress(f"Disk Grid: {disk_grid}")
    """
    Disk Grid example:
    [{'path': 'E', 'size': 50}, {'path': 'F', 'size': 100}]
    """

    disk_number = 1
    for disk in disk_list:
        param_name = f'disk_{disk_number}_size'
        param_label = f'Disk {disk_number} Size (GB)'
        create_custom_field(param_name, param_label, "INT")
        server.set_value_for_custom_field(param_name, disk['size'])
        disk_number += 1
    return "SUCCESS", "", ""