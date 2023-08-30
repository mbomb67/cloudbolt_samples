#!/usr/local/bin/python
from django.utils.text import slugify

from common.methods import set_progress
from network_virtualization.models import NetworkVirtualization
from network_virtualization.nsx_t.models import NSXTLogicalRouterGateway
from resources.models import SoftwareDefinedNetwork, SoftwareDefinedNetworkAppliance
from utilities.exceptions import CloudBoltException

from utilities.logger import ThreadLogger


logger = ThreadLogger(__name__)


def generate_nsx_obj_name(context):
    return "{}-{}".format(
        slugify(context["resource_name"]),
        "-".join([slugify(si.name) for si in context["server_service_items"]]),
    )


def run(job, **kwargs):
    resource = kwargs.get("resource")
    params = job.job_parameters.cast()
    if not hasattr(params, "network_service_item"):
        raise CloudBoltException(
            "Create virtual network item not supported without a network "
            "service item blueprint context!"
        )
    bp_job_params = job.parent_job.job_parameters.cast()
    resource_name = resource.name
    network_service_item = params.network_service_item
    set_progress(
        "Creating a network as part of deploying {} '{}' with ip schema '{}'".format(
            resource.resource_type.label, resource_name, network_service_item.ipv4_block
        )
    )

    # The job parameters of the parent deploy BP job should be a
    # BlueprintOrderItem
    prov_server_bias = bp_job_params.blueprintitemarguments_set.filter(
        service_item__in=network_service_item.servers.all()
    )

    if not prov_server_bias:
        raise CloudBoltException(
            "Create virtual network expects at least one Provision Server Service Item"
        )
    environment = prov_server_bias[0].environment

    advanced_network_cfvs = environment.custom_field_options.filter(
        field__namespace__name="advanced_networking"
    )
    if advanced_network_cfvs:
        set_progress(
            "The advanced networking parameters being used are: [{}]".format(
                ", ".join(["'{}'".format(cfv) for cfv in advanced_network_cfvs])
            )
        )
    else:
        raise CloudBoltException(
            "Create virtual network is depended on advanced parameters not found in environment"
            " '{}'".format(environment)
        )

    context = {
        "resource_name": resource_name,
        "server_service_items": [bia.service_item for bia in prov_server_bias],
    }
    network_name = generate_nsx_obj_name(context)
    set_progress(
        "The auto-generated name for the new virtual network is: {}".format(
            network_name
        )
    )

    rh = environment.resource_handler.cast()
    network, identifier, nsx_api = rh.create_advanced_network(
        network_name, network_service_item.ipv4_block, advanced_network_cfvs
    )

    set_progress("Network: {} [{}], ID: {}".format(network, network.id, identifier))

    #  Create a SDN entry to associate the network with the resource
    sdn = SoftwareDefinedNetwork.objects.create(
        name=network_name,
        resource_handler=rh,
        environment=environment,
        resource=resource,
        network=network,
        identifier=identifier,
    )

    logger.debug("Created SoftwareDefinedNetwork: {}".format(sdn))

    for bia in prov_server_bias:
        setattr(bia, "sc_nic_0", network)

    if network_service_item == bp_job_params.blueprint.nsis().last():
        # this is the last network service item being deployed.... go ahead and attach interfaces to
        # an ESG or LDR, creating it if needed

        prov_server_bias = bp_job_params.blueprintitemarguments_set.filter(
            service_item__in=bp_job_params.blueprint.pssis()
        )

        # we generate a name to be used if we are creating a new gateway
        # is simpler to generate the potential name now than to duplicate code and
        # pass around extra context
        gateway_naming_context = {
            "resource_name": resource_name,
            "server_service_items": [bia.service_item for bia in prov_server_bias],
        }
        new_gateway_name = generate_nsx_obj_name(gateway_naming_context)

        gateway = None
        # If a NetworkVirtualization mapping exists for this RH, we assume NSX-T
        if rh.sdn_mapping.count() > 0:
            gateways = advanced_network_cfvs.filter(field__name="nsxt_tier_1")
            # If we have exactly one T1 gateway, use it
            if gateways.count() == 1:
                gateway_cfv = gateways.get()
                gateway = NSXTLogicalRouterGateway.objects.get(
                    pk=int(gateway_cfv.value)
                )
            # Otherwise, we create a new one
            else:
                # Throws an exception if no T0s are found, or if more than one
                nsxt_tier_0 = advanced_network_cfvs.filter(
                    field__name="nsxt_tier_0"
                ).get()

                logger.info(f"Getting Tier 0 object with pk { int(nsxt_tier_0.value) }")
                tier_0 = NSXTLogicalRouterGateway.objects.get(pk=int(nsxt_tier_0.value))
                new_gateway = nsx_api.add_tier1_gateway(
                    new_gateway_name, new_gateway_name, tier_0.display_name
                )
                nsxt = NetworkVirtualization.objects.filter(
                    mappings__resource_handler=rh
                ).first()
                gateway = NSXTLogicalRouterGateway.objects.create(
                    display_name=new_gateway["id"],
                    uuid=new_gateway["id"],
                    network_virtualization=nsxt,
                    router_type="TIER1",
                )

            # Set attributes for creating the SDN appliance below, and attach segments
            sdn_name = gateway.display_name
            sdn_id = gateway.uuid

        # Otherwise, we use NSX-V
        else:
            edges = advanced_network_cfvs.filter(field__name="nsx_edge")
            if edges.count() == 1:
                gateway = edges.get().value
            else:
                appliance_size = (
                    "large" if bp_job_params.blueprint.lbsis().exists() else "compact"
                )
                gateway = nsx_api.create_edge(
                    new_gateway_name, appliance_size, resource=resource
                )
                gateway.save()

            # Set attributes for creating the SDN appliance below, and attach segments
            sdn_name = gateway.name
            sdn_id = gateway.object_id

        for sdn in resource.softwaredefinednetwork_set.all():
            gateway.attach_interface(sdn)
            # also attach the vxlans to the group
            resource.group.sc_nic_0 = sdn.network

        # Create the SDN on the RH
        sdn_appliance = SoftwareDefinedNetworkAppliance.objects.create(
            name=sdn_name,
            resource_handler=rh,
            environment=environment,
            resource=resource,
            identifier=sdn_id,
        )

        set_progress(
            "Done associating gateway appliance '{}' with {}".format(
                sdn_appliance, resource.resource_type.label
            )
        )
    return "", "", ""
