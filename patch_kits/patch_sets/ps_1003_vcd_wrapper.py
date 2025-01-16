"""
Patch kit that adds vCD support for:
- vCD Storage Profiles by patching the create_vm method in the
VCDTechnologyWrapper class.
- vCD Organizations by patching the get_current_organizations_as_dict method
to fix a bug where environments were getting confused and showing images on
multiple envs.
"""
from orders.models import CustomFieldValue
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def patch_technology_wrapper_for_vcd():
    import xml.etree.ElementTree as etree
    from resourcehandlers.vcloud_director.vcd_wrapper import (
        vcd_xml_tag, VCDTechnologyWrapper, XMLNS
    )

    def create_vm(
            self,
            name="vcd-vm",
            mem_size=1,
            cpu_cnt=1,
            prov_timeout=7200,
            vdc_uuid=None,
            image_uuid=None,
            storage_profile_uuid=None,
            description="",
            network_uuid=None,
            network_name=None,
            ip_allocation_mode="NONE",
            network_adapter_type=None,
            **kwargs,
    ):
        """
        Create a VM in vCloudDirector given the base image uuid and return a dict of {'uuid': vm_uuid}.

        VM creation is an asynchronous API operation and will pass through the task poller.

        Args:
            image_uuid (str): The uuid of the image to use.
            name (str): The new server's name.
            mem_size (decimal.Decimal): The new server's amount of memory, in GB.
            cpu_cnt (int): The number of VCPUs to assign the server.
            prov_timeout (int): Time, in seconds, before giving up on Acropolis provisioning the server.

        Returns:
            dict: {'uuid': vm_uuid} for the UUID of the successfully-provisioned VM.

        Raises:
            VCDError: If a vCloud Director task fails and returns an error code and optional error message.
        """
        instantiation_body = """
        <InstantiateVAppTemplateParams
          xmlns="http://www.vmware.com/vcloud/v1.5"
          name="{name}"
          deploy="true"
          powerOn="true"
          xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
          xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1">
            <Description>{description}</Description>
            {network_config_section}
            <Source href="https://vcloud.example.com/api/vAppTemplate/{image_uuid}"/>
            <SourcedItem>
                <Source href="{image_vm_href}"/>
                <VmGeneralParams>
                    <Name>{name}</Name>
                    <Description>{description}</Description>
                    <NeedsCustomization>true</NeedsCustomization>
                </VmGeneralParams>
                <InstantiationParams>
                    <ovf:VirtualHardwareSection
                        xmlns:ovf="http://schemas.dmtf.org/ovf/envelope/1"
                        xmlns:rasd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_ResourceAllocationSettingData"
                        xmlns:vmw="http://www.vmware.com/schema/ovf"
                        xmlns:vcloud="http://www.vmware.com/vcloud/v1.5"
                        xmlns:vssd="http://schemas.dmtf.org/wbem/wscim/1/cim-schema/2/CIM_VirtualSystemSettingData"
                        ovf:transport=""
                        vcloud:href="https://vcloud.example.com/api/vApp/vm-4/virtualHardwareSection/"
                        vcloud:type="application/vnd.vmware.vcloud.virtualHardwareSection+xml">
                        <ovf:Info>Virtual hardware requirements</ovf:Info>
                    </ovf:VirtualHardwareSection>
                    {network_conn_section}
                </InstantiationParams>
                <StorageProfile href="{storage_profile_uuid}">
                </StorageProfile>
            </SourcedItem>
            <AllEULAsAccepted>true</AllEULAsAccepted>
        </InstantiateVAppTemplateParams>
        """

        image_vm = self.get_vm_element(image_uuid, from_template=True)

        network_config_section = ""
        network_conn_section = ""
        if network_name and network_uuid:
            network_href = f"{self.client.base_url_v1}/network/{network_uuid}"
            network_config_section = f"""
            <InstantiationParams>
                <NetworkConfigSection>
                    <ovf:Info>Configuration parameters for logical networks</ovf:Info>
                    <NetworkConfig networkName="{network_name}">
                    <Configuration>
                        <ParentNetwork href="{network_href}" name="#{network_name}" type="application/vnd.vmware.vcloud.network+xml"/>
                        <FenceMode>bridged</FenceMode>
                    </Configuration>
                    </NetworkConfig>
                </NetworkConfigSection>
            </InstantiationParams>
            """

            ip_address = kwargs.get("ip")
            ip_address_elem = ""
            if ip_address:
                ip_address_elem = f"<IpAddress>{ip_address}</IpAddress>"

            network_adapter_type_elem = ""
            if network_adapter_type:
                network_adapter_type_elem = (
                    f"<NetworkAdapterType>{network_adapter_type}</NetworkAdapterType>"
                )

            network_conn_section = f"""
            <NetworkConnectionSection>
                <ovf:Info>Configure Primary Nic</ovf:Info>
                <PrimaryNetworkConnectionIndex>0</PrimaryNetworkConnectionIndex>
                <NetworkConnection network="{network_name}">
                    <NetworkConnectionIndex>0</NetworkConnectionIndex>
                    {ip_address_elem}
                    <IsConnected>true</IsConnected>
                    <IpAddressAllocationMode>{ip_allocation_mode}</IpAddressAllocationMode>
                    {network_adapter_type_elem}
                </NetworkConnection>
            </NetworkConnectionSection>
            """

        payload_xml = etree.fromstring(
            instantiation_body.format(
                name=name,
                description=description,
                image_uuid=image_uuid,
                image_vm_href=image_vm.attrib["href"],
                network_config_section=network_config_section,
                network_conn_section=network_conn_section,
                storage_profile_uuid=storage_profile_uuid,
            )
        )

        vhs = (
            payload_xml.find(vcd_xml_tag("SourcedItem"))
            .find(vcd_xml_tag("InstantiationParams"))
            .find("ovf:VirtualHardwareSection", XMLNS)
        )
        cpu_element = self.get_updated_cpu_item(
            self.get_matching_element(image_vm, "cpu_cnt"), cpu_cnt
        )

        memory_element = self.get_updated_mem_item(
            self.get_matching_element(image_vm, "mem_size"), mem_size
        )

        vhs.insert(1, cpu_element)
        vhs.insert(1, memory_element)
        payload = etree.tostring(payload_xml)
        logger.info(payload)

        uri = f"vdc/{vdc_uuid}/action/instantiateVAppTemplate"
        vapp_xml = self.client.post(uri, payload).text
        vapp = etree.fromstring(vapp_xml)
        task = vapp.find(vcd_xml_tag("Tasks/Task"))
        task_uuid = task.attrib.get("href").split("/")[-1]
        self.client.wait_for_task(task_uuid, prov_timeout)

        # refresh vAPP and get the vm_uuid
        vapp_xml = self.client.get(vapp.attrib.get("href")).text
        vapp = etree.fromstring(vapp_xml)
        vm = vapp.find(vcd_xml_tag("Children/Vm"))
        vm_id = vm.attrib.get("href").split("/")[-1]
        return {"uuid": vm_id}

    VCDTechnologyWrapper.create_vm = create_vm


def patch_vcd_model_get_create_resource_args():
    from resourcehandlers.vcloud_director.models import (
        VCDHandler
    )

    def get_create_resource_kwargs(self, server):
        """
        Build a dictionary of kwargs to pass to wrapper.create_vm()

        Extends kwargs from (via super) resourcehandlers.models.ResourceHandlerSubmodel.get_create_resource_kwargs().

        This looks for Parameters (Custom Fields) matching the following names during resource creation:
            cpu_cnt: The number of VCPUs allocated for the VM.
            mem_size: The amount of RAM, in GiB, allocated for the VM.
            prov_timeout: Time, in seconds, to wait before giving up on the provisioning task.
            (And everything from resourcehandlers.models.ResourceHandlerSubmodel.get_create_resource_kwargs())

        Args:
            server (infrastructure.models.Server): A CloudBolt Server instance representing the Acropolis VM to build.

        Returns:
            dict: of kwargs for passing to wrapper.create_vm().
        """
        creds = self.get_server_credentials(server.id)

        kwargs = {
            "name": server.get_vm_name(),
            "username": creds.get("username", None),
            "password": creds.get("password", None),
            "is_windows": server.os_build.is_windows(),
        }

        # Currently assuming only 1 NIC
        if self.has_network_support():
            network = server.sc_nic_0
            if network:
                network = network.cast()
                # Check to see if the Server object has an override
                # for th network adapter type.
                adapter_type = server.get_value_for_custom_field(
                    "vcd_network_adapter_type"
                )

                kwargs["network"] = network
                kwargs["network_uuid"] = network.network.split(":")[-1]
                kwargs["network_name"] = network.name
                kwargs[
                    "network_adapter_type"] = adapter_type or network.adapter_type

                if network.addressing_schema == "dhcp":
                    kwargs["ip_allocation_mode"] = "DHCP"
                elif network.addressing_schema == "static":
                    kwargs["ip_allocation_mode"] = "MANUAL"
                else:
                    kwargs["ip_allocation_mode"] = "NONE"

            ip = server.sc_nic_0_ip
            if ip:
                kwargs["ip"] = ip

        if server.cpu_cnt:
            kwargs["cpu_cnt"] = server.cpu_cnt

        if server.mem_size:
            kwargs["mem_size"] = server.mem_size

        if hasattr(server, "prov_timeout"):
            kwargs["prov_timeout"] = server.prov_timeout

        vcdbaseimage = self.get_osba_for_osb_and_env(
            server.os_build, server.environment
        )
        kwargs["image_uuid"] = vcdbaseimage.uuid

        storage_profile = server.vcd_storage_profile
        if storage_profile:
            storage_profile = storage_profile.href  # .split("/")[-1]
        kwargs["storage_profile_uuid"] = storage_profile

        vdc = self.get_env_virtual_datacenter(server.environment)
        kwargs["vdc_uuid"] = vdc.value[0]

        if server.annotation:
            from common.methods import generate_string_from_template_for_server

            description = generate_string_from_template_for_server(
                server.annotation, server
            )
        else:
            description = "N/A"
        kwargs["description"] = description

        return kwargs

    VCDHandler.get_create_resource_kwargs = get_create_resource_kwargs


def patch_vcd_model_get_current_organizations():
    from resourcehandlers.vcloud_director.models import (
        VCDHandler
    )

    def get_current_organizations(self, with_templates=False):
        """
        Return a list of dicts containing info about each org on this RH.
        Args:
            with_templates: if True, include a key `templates` having a list of all OSBAs
                associated with each org.
        """
        orgs = []
        current_org_names = self.current_locations()
        for org_name in current_org_names:
            org = {}
            org["name"] = org_name
            org["vdcs"] = []
            vdc_uuids = []
            for env in self.environment_set.all():
                try:
                    vcd_org = env.custom_field_options.get(
                        field__name="vcd_organization").str_value.split(":")[1]
                except Exception as e:
                    logger.warning(f"Could not get org name from env {env}: "
                                   f"{e}")
                    continue
                if vcd_org != org_name:
                    continue
                if "uuid" not in org:
                    # we need the org uuid too
                    org_cfv = self.get_env_organization(env)
                    org["uuid"] = org_cfv.value[0]
                vdc = self.get_env_virtual_datacenter(env)
                if vdc:
                    uuid, name = vdc.value
                    if uuid not in vdc_uuids:
                        # first time we see this uuid, so we add it
                        org["vdcs"].append({"uuid": uuid, "name": name})
                        vdc_uuids.append(uuid)

            if with_templates:
                org["templates"] = self.get_images_for_location(org["uuid"])

            orgs.append(org)
        return orgs

    VCDHandler.get_current_organizations_as_dict = get_current_organizations

