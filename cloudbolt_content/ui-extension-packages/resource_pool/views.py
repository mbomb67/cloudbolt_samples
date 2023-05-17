"""
The Resource Pool XUI creates a tab on a CloudBolt Group that displays the
Resource Pool name and the current capacities associated with that Resource
Pool. The tab is only displayed if the group has a single value for the
group_vmware_resourcepool custom field.

The Resource Pool XUI will then loop through every resource handler where the
group has at least a single environment enabled and find all resource pools
grouped by Resource Handler > Datacenter > Cluster > Resource Pool. The
Resource Pool XUI will then display the Resource Pool name and the current
capacities associated with that Resource Pool.
"""

from django.shortcuts import render

from accounts.models import Group
from extensions.views import tab_extension, TabExtensionDelegate
from pyVmomi import vim
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


class ResourcePoolGroupTabDelegate(TabExtensionDelegate):

    def should_display(self):
        group = self.instance
        rp_name = get_rp_name(group)
        if rp_name:
            return True


def get_rp_name(group):
    cfvs = group.get_cfvs_for_custom_field("group_vmware_resourcepool")
    if len(cfvs) == 1:
        return cfvs.first().value
    logger.debug(f"Resource Pool name could not be determined. {len(cfvs)} "
                 f"values were found for group_vmware_resourcepool.")
    return None


@tab_extension(model=Group, title="Resource Pool",
               description="View Data for your Resource Pool",
               delegate=ResourcePoolGroupTabDelegate)
def resource_pool_tab(request, obj_id):
    group = Group.objects.get(id=obj_id)
    rp_name = get_rp_name(group)
    context = {
        "rp_name": rp_name,
        "group": group,
        "rps_data": get_rps_data(rp_name, group),
    }
    logger.info(f"Context: {context}")
    return render(request, 'resource_pool/templates/group_tab.html',
                  context=context)


def get_rps_data(rp_name, group):
    rps_data = {}
    envs = group.environments.filter(
        resource_handler__resource_technology__name="VMware vCenter"
    )
    for env in envs:
        rh = env.resource_handler.cast()
        try:
            rps_data[rh.name]
            logger.info(f"Resource Pool data already exists for {rh.name}, "
                        f"skipping.")
            continue
        except KeyError:
            rps_data[rh.name] = {}
        wrapper = rh.get_api_wrapper()
        si = wrapper._get_connection()
        content = si.RetrieveContent()
        root_folder = content.rootFolder
        view_ref = content.viewManager.CreateContainerView(
            container=root_folder,
            type=[vim.ResourcePool],
            recursive=True)
        resource_pools = view_ref.view
        for rp in resource_pools:
            if rp.name == rp_name:
                datacenter, cluster, rp_path, rp_data = get_rp_data(rp)
                rps_data = add_data_to_rps_data(rps_data, rh.name, datacenter,
                                                cluster, rp_path, rp_name,
                                                rp_data)

    return rps_data


def add_data_to_rps_data(rps_data, rh_name, datacenter, cluster, rp_path,
                         rp_name, rp_data):
    rps_data = add_key_to_dict_if_not_exists(rps_data, rh_name)
    rps_data[rh_name] = add_key_to_dict_if_not_exists(
        rps_data[rh_name],
        datacenter
    )
    rps_data[rh_name][datacenter] = add_key_to_dict_if_not_exists(
        rps_data[rh_name][datacenter],
        cluster
    )
    rps_data[rh_name][datacenter][cluster][f'{rp_path}{rp_name}'] = rp_data
    return rps_data


def add_key_to_dict_if_not_exists(d, key):
    try:
        d[key]
    except KeyError:
        d[key] = {}
    return d


def get_rp_data(rp):
    parent_rp = rp.parent
    rp_path = ""
    while parent_rp.name != "Resources":
        if rp_path:
            rp_path = f"{parent_rp.name}/{rp_path}"
        else:
            rp_path = f"{parent_rp.name}/"
        parent_rp = parent_rp.parent

    cluster = rp.parent
    while type(cluster) != vim.ClusterComputeResource:
        cluster = cluster.parent
    datacenter = cluster.parent
    while type(datacenter) != vim.Datacenter:
        datacenter = datacenter.parent
    mem_limit_gb = rp.config.memoryAllocation.limit/1024
    mem_usage_gb = rp.summary.runtime.memory.reservationUsed/1024/1024/1024
    mem_percent_used = mem_usage_gb / mem_limit_gb * 100
    mem_percent_free = 100 - mem_percent_used
    rp_data = {
        "mem_limit_gb": round(mem_limit_gb, 2),
        "mem_usage_gb": round(mem_usage_gb, 2),
        "mem_percent_used": round(mem_percent_used, 2),
        "mem_percent_free": round(mem_percent_free, 2),
    }

    return datacenter.name, cluster.name, rp_path, rp_data

