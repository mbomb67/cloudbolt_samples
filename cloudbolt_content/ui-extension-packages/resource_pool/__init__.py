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