import logging
from datetime import datetime, timezone
from django.conf import settings
from keystoneauth1.identity import v3
from keystoneauth1 import session, exceptions
from infrastructure.models import Server
from openstack import connection
from libcloud.compute.providers import get_driver
from libcloud.compute.types import Provider
import libcloud.security
import requests
import base64
from common.methods import get_proxies
from resourcehandlers.openstack.models import OpenStackImage
from resourcehandlers.openstack.models import OpenStackHandler
from resourcehandlers.openstack.openstack_wrapper import TechnologyWrapper
from resourcehandlers.openstack.data_collector import OpenStackDataCollector
from utilities.exceptions import CloudBoltException, IllegalStateException
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def patch_openstack_auth():
    """
    This patch is to fix an issue for Platform9 where the auth url isn't
    located at the traditional port 5000, but rather at the standard
    HTTPS port 443, but with a /keystone suffix.
    5000 is still used for other OpenStack deployments.

    Patches the following methods:
    - OpenStackHandler._generate_token
    - TechnologyWrapper.initialize_driver
    - OpenStackDataCollector.generate_token
    """

    def _generate_token(self):
        """
        This method creates identity token for the OpenStack ResourceHandler
        For generating token, Username, Password, Domain and Project will be used as authentication
        """
        resourcehandler = self
        BASE_URL = f"{self.protocol}://{self.ip}:{self.port}"
        data = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": self.serviceaccount,
                            "domain": {"name": self.cast().domain or "Default"},
                            "password": self.servicepasswd,
                        }
                    },
                },
            }
        }
        if self.project_id:
            data["auth"]["scope"] = {"project": {"id": self.cast().project_id}}
        # Adjust BASE_URL for common HTTPS ports
        if self.port == 443 or self.port == 8443:
            BASE_URL = f"{BASE_URL}/keystone"
        url = f"{BASE_URL}/v3/auth/tokens"
        r = requests.post(url, json=data, verify=self.enable_ssl_verification)
        if r.status_code != 201:
            failing_reason = "Unknown"
            if r.json().get("error"):
                failing_reason = r.json().get("error").get("message")
            raise Exception(f"Token generation failed: {failing_reason}")
        if self.name:
            resourcehandler.api_auth_token = r.headers["X-Subject-Token"]
            resourcehandler.auth_token_expiry = datetime.strptime(
                r.json()["token"].get("expires_at"), "%Y-%m-%dT%H:%M:%S.%fZ"
            )
            resourcehandler.save()
        return str(r.headers["X-Subject-Token"])

    def initialize_driver(
            self,
            ip,
            port,
            username,
            password,
            ssl_verification=False,
            protocol="http",
            **kwargs,
    ):
        """
        Overrides the parent LibcloudTechnologyWrapper method to return an
        OpenStack specific driver.

        Also initializes a keystone connection that is required to list projects/tenants.

        This patch is to fix an issue for Platform9 where the auth url isn't
        located at the traditional port 5000, but rather at the standard
        HTTPS port 443, but with a /keystone suffix.
        5000 is still used for other OpenStack deployments.
        """
        self.ssl_verification = ssl_verification
        auth_url = "{}://{}:{}".format(protocol, ip, port)
        if port == 443 or port == 8443:
            auth_url = f"{auth_url}/keystone"
        tenant = kwargs.pop("location_name", None)
        self.domain = kwargs.pop("domain", None)
        driver_kwargs = dict(
            ex_force_auth_url=auth_url,
            ex_force_auth_version="2.0_password",
            ex_tenant_name=tenant,
            proxy_url=kwargs.pop("proxy_url", None),
        )

        """
        The ssl_verification kwarg should be passable to Libcloud drivers, which should
        then pass it on to the requests lib. However, those classes don't seem to accept
        a CA Certs path. Libcloud wraps requests, but doesn't pass kwargs, it looks up
        properties like CA_CERTS_PATH set on itself.
        Instead, we will set SSL Verification flags on the global libcloud instance.
        """
        if ssl_verification:
            libcloud.security.CA_CERTS_PATH = [ssl_verification]
        else:
            libcloud.security.VERIFY_SSL_CERT = False

        cls = get_driver(Provider.OPENSTACK)
        driver = cls(username, password, **driver_kwargs)

        from novaclient.client import Client as N
        from cinderclient.client import Client as C

        # This suppresses any DEBUG level logging messages that happen in the
        # keystoneclient.sessions module. We do this because the debug logging
        # messages in this module include the user's openstack account password and Auth
        # token. Not setting this log level is a huge security risk.
        key_logger = logging.getLogger("keystoneclient.session")
        if not settings.DEBUG:
            key_logger.setLevel(logging.INFO)
            # this iso8601 module just logs info about the date and time, so
            # we'll suppress it too
            logging.getLogger("iso8601.iso8601").setLevel(logging.INFO)
        # if the logger is a job logger, make sure the logs from keystoneclient
        # go to the job log
        key_logger.addHandler(logger)

        project_id = kwargs.pop("project_id", None)
        auth_policy = kwargs.pop("auth_policy", None)
        self.identity_version = "2"
        if "A3" in auth_policy:
            auth = v3.Password(
                auth_url="{}/v3".format(auth_url),
                username=username,
                password=password,
                user_domain_name=self.domain,
                project_id=project_id,
            )
            sess = session.Session(auth=auth, verify=self.ssl_verification)
            if "I3" in auth_policy:
                from keystoneclient.v3.client import Client as K

                self.identity_version = "3"
                self.keystone = K(
                    session=sess, project_id=project_id
                )
            else:
                auth_url = "{}/v2.0/".format(auth_url)
                from keystoneclient.v2_0.client import Client as K

                self.keystone = K(
                    username=username,
                    password=password,
                    auth_url=auth_url,
                    insecure=True,
                )

            compute_version = 2
            if "C3" in auth_policy:
                compute_version = 3
            # Use openstacksdk to make a connection to openstack RH
            # BestPractice: Default to using the openstack SDK over specific APIs like nova

            # this connection (and the tenant specific one below)
            # no longer specifies identity_interface="internal"
            # because not all openstack deployments register an "internal" endpoint for keystone
            self.connection = connection.Connection(
                session=sess, compute_api_version="2"
            )
            self.nova = N(compute_version, session=sess)
            self.cinder = C(compute_version, session=sess)

            if tenant:
                # should instantiate the nova client for the correct project instead of the default
                # project. Note that this requires a functioning keystone client, therefore this
                # cannot be combined with the code above and we indeed need to re-create the auth
                # and sess here instead

                # tenant_specific nova is used to create new servers in the correct project
                project_id = self.get_location_by_name(tenant).id
                auth = v3.Password(
                    auth_url="{}/v3".format(auth_url),
                    username=username,
                    password=password,
                    user_domain_name=self.domain,
                    project_id=project_id,
                )
                sess = session.Session(auth=auth, verify=self.ssl_verification)
                self.connection = connection.Connection(
                    session=sess, compute_api_version="2"
                )

                # Override self.nova to use the tenant-specific one by default. (This used to be tenant_specific_nova
                # but that was not being used.)
                self.nova = N(compute_version, session=sess,
                              project_id=project_id)
                logger.info(
                    f"Connected to nova with a tenant-specific session with tenant '{tenant}'."
                )
        else:
            auth_url = "{}/v2.0/".format(auth_url)
            from keystoneclient.v2_0.client import Client as K

            self.keystone = K(
                username=username, password=password, auth_url=auth_url,
                insecure=True
            )

            self.nova = N(
                2,
                username=username,
                api_key=password,
                project_id=tenant,
                auth_url=auth_url,
                insecure=True,
            )
            self.cinder = C(
                2,
                username=username,
                api_key=password,
                project_id=tenant,
                auth_url=auth_url,
                insecure=True,
            )

        return driver

    def generate_token(self, project_id=None) -> None:
        """
        Generates a new authentication token using the service account credentials.

        Raises:
            Exception: If the token generation fails.
        """
        rh = self.resource_handler
        BASE_URL = f"{rh.protocol}://{rh.ip}:{rh.port}"
        if rh.port == 443 or rh.port == 8443:
            BASE_URL = f"{BASE_URL}/keystone"
        url = f"{BASE_URL}/v3/auth/tokens"
        payload = {
            "auth": {
                "identity": {
                    "methods": ["password"],
                    "password": {
                        "user": {
                            "name": rh.serviceaccount,
                            "domain": {
                                "name": rh.domain or "Default"
                            },
                            "password": rh.servicepasswd,
                        }
                    },
                },
            }
        }
        # If project_id is provided, request a project-scoped token
        if project_id:
            payload["auth"]["scope"] = {"project": {"id": project_id}}
        headers = {"Content-Type": "application/json"}

        proxies = get_proxies(self.resource_handler.ip)

        # Sending POST request to generate the token
        response = requests.post(
            url,
            json=payload,
            headers=headers,
            verify=self.resource_handler.get_ssl_verification(),
            proxies=proxies,
        )
        if response.status_code == 201:
            self.api_auth_token = response.headers["X-Subject-Token"]
            self.auth_token_expiry = datetime.strptime(
                response.json()["token"]["expires_at"], "%Y-%m-%dT%H:%M:%S.%fZ"
            ).replace(tzinfo=timezone.utc)
        else:
            raise Exception(
                f"Token generation failed: {response.json()['error']['message']}"
            )

    OpenStackHandler._generate_token = _generate_token
    TechnologyWrapper.initialize_driver = initialize_driver
    OpenStackDataCollector.generate_token = generate_token


def patch_openstack_create_instance():
    """
    This patch provides enhancements to work with Platform9 OpenStack
    deployments:
     1. Supports boot from volume when the disk_size parameter is provided. If
     disk_size = 0, then it will use ephemeral storage.
     2. Support for Hotplug. When a flavor has hotplug enabled (vcpus=0 or
     ram=0), the cpu_cnt and mem_size parameters must be provided to set the
     desired CPU and Memory sizes along with the flavor.
    """

    def create_instance(
            self,
            name,
            image,
            size,
            networks,
            key_name=None,
            security_groups=None,
            availability_zone=None,
            userdata=None,
            ip: str = None,
            disk_size: int = None,
            cpu_cnt: int = None,
            mem_size: int = None,
    ):
        """
        Creates an instance in openstack and return its ID
        """
        # TODO: assert(len(networks) > 0)
        # convert the list of CB ResourceNetworks into a list of OpenStackNetworks
        os_networks = []
        logger.info(f"NETWORKS IS {networks}")
        for net in networks:
            os_network = {"net-id": net.network}
            logger.debug(
                "CB network key: {}, OpenStack net: {}".format(net.network,
                                                               os_network)
            )
            os_networks.append(os_network)

        # convert size to flavor
        flavor = self.get_size_object(size)

        # Check if the image is a volume image.
        try:
            image = OpenStackImage.objects.get(
                template_name=image.template_name)

        except OpenStackImage.MultipleObjectsReturned:
            logger.warning(
                f"More than one image exists under the template_name {image.template_name}. Grabbing the first."
            )
            image = OpenStackImage.objects.filter(
                template_name=image.template_name
            ).first()

        if image.is_bootable is True:
            net = networks[0].network

            # convert cb image to openstack image
            volume_dict = self.connection.search_volumes(image.external_id)
            if volume_dict:
                logger.info(
                    "Creating OpenStack server with name='{}' from volume, volume='{}', flavor='{}', "
                    "networks='{}' key_name='{}', security_groups='{}', and availability_zone='{}'".format(
                        name,
                        image,
                        flavor,
                        net,
                        key_name,
                        security_groups,
                        availability_zone,
                    )
                )
                server_dict = {
                    "name": name,
                    "flavor": flavor,
                    "boot_volume": volume_dict[0],
                    "network": net,
                    "key_name": key_name,
                }
                # Only include security_groups and availability_zone if there are any
                if security_groups:
                    server_dict["security_groups"] = security_groups
                if availability_zone:
                    server_dict["availability_zone"] = availability_zone
                self.connection.create_server(**server_dict)
            try:
                instance = self.connection.search_servers(name)
                return instance[0]["id"]
            except IndexError as e:
                raise e
        else:
            image = self.connection.image.get_image(image.external_id)
            logger.info(
                f"Creating OpenStack node with name='{name}', image='{image}',"
                f" flavor='{flavor}', networks='{os_networks}' "
                f"key_name='{key_name}', security_groups='{security_groups}', "
                f"availability_zone='{availability_zone}', "
                f"disk_size='{disk_size}', cpu_cnt='{cpu_cnt}', "
                f"mem_size='{mem_size}'"
            )
            if ip is not None and ip != "":
                os_networks[0]["v4-fixed-ip"] = ip

            # Use nova instead of libcloud.create_node because it lacks direct userdata support
            # (might be supported by "ex_metadata" kwargs)
            # Uses a tenant-specific nova to create servers in the correct tenant.
            # loop through each of the os_networks to replace the net-id with
            # the uuid
            for net in os_networks:
                net_id = net.get("net-id")
                net["uuid"] = net_id
                del net["net-id"]

            create_kwargs = {
                "name": name,
                "image_id": image.id,
                "flavor_id": flavor.id,
                "networks": os_networks,
                "key_name": key_name,
                "availability_zone": availability_zone,
                "security_groups": [{"name": sg} for sg in security_groups],
                "block_device_mapping_v2": [],  # This triggers modern BDMv2 path
            }

            if userdata:
                create_kwargs["user_data"] = base64.b64encode(userdata.encode('utf-8')).decode('utf-8')

            if flavor.disk == 0 and not disk_size:
                raise CloudBoltException(
                    "Flavor has no disk. A disk_size must be provided for boot"
                    " from volume Flavors."
                )

            if disk_size:
                # Updating to support block device mapping for boot from volume
                bdm_v2 = [{
                    "boot_index": 0,
                    "source_type": "image",
                    "destination_type": "volume",
                    "uuid": image.id,
                    "volume_size": disk_size,
                    # "volume_type": "dg-nfs",     # outdated CMP sdk version does not support this
                    "delete_on_termination": True,
                }]
                create_kwargs["block_device_mapping_v2"] = bdm_v2

            if self.get_flavor_hotplug(flavor):
                if not cpu_cnt or not mem_size:
                    raise CloudBoltException(
                        "CPU count and Memory size must be provided for hotplug flavors."
                    )
                # Set the vCPU and RAM overrides for hotplug flavors
                create_kwargs["metadata"] = {
                    "HOTPLUG_MEMORY": str(cpu_cnt),
                    "HOTPLUG_MEMORY_MAX": "65536",
                    "HOTPLUG_CPU": str(int(mem_size)),
                    "HOTPLUG_CPU_MAX": "64",
                }

            instance = self.connection.compute.create_server(**create_kwargs)
            logger.debug(f"New OpenStack instance has id: {instance.id}")
            return instance.id

    def get_flavor_hotplug(self, flavor):
        """
        Determine the disk size to use for boot from volume based on flavor and
        image.
        """
        if flavor.vcpus == 0 or flavor.ram == 0:
            logger.debug("Hotplugging is required for this flavor.")
            return True
        logger.debug("Hotplugging is not required for this flavor.")
        return False

    def create_resource(self, resource_id, use_template):
        """
        Use the server identified by resource_id to prepare parameters required
        to create a new openstack instance.  Once the node has been created,
        store its ID in server.resource_handler_svr_id and return the ID
        """
        server = Server.objects.get(id=resource_id)
        wrapper, _, _ = self.setup_connection(resource_id)

        # TODO: Refactor the image related code block to parent class.  All RHs
        # look at os_build_attributes and raise exception if more than one
        images = self.os_build_attributes.filter(os_build=server.os_build)
        if not images:
            raise CloudBoltException(
                "No Image for this OS Build is associated with OpenStack Handler ({})".format(
                    self
                )
            )
        if len(images) > 1:
            raise CloudBoltException(
                "More than one Image for this OS Build "
                "is associated with OpenStack Handler ({})".format(self)
            )
        image = images[0].cast()
        networks = self.get_networks_for_node(server)

        name = server.get_vm_name()
        size = server.node_size
        key_name = server.get_value_for_custom_field("key_name")
        security_groups = server.get_value_for_custom_field("sec_groups")
        availability_zone = server.get_value_for_custom_field(
            "os_availability_zone")
        userdata = server.get_value_for_custom_field("os_user_data")
        if userdata is not None and userdata != "":
            from common.methods import generate_string_from_template_for_server

            userdata = generate_string_from_template_for_server(userdata,
                                                                server)

        uuid = wrapper.create_instance(
            name,
            image,
            size,
            networks,
            key_name,
            security_groups,
            availability_zone,
            userdata,
            server.ip,
            server.disk_size,
            server.cpu_cnt,
            server.mem_size,
        )
        server.resource_handler_svr_id = uuid
        server.save()
        return uuid

    TechnologyWrapper.get_flavor_hotplug = get_flavor_hotplug
    TechnologyWrapper.create_instance = create_instance
    OpenStackHandler.create_resource = create_resource


def patch_openstack_error_response():
    """ 
    Patches an issue where errors encountered in OpenStack are not surfaced in
    CloudBolt
    """
    
    def get_server_power_and_task_state(self, server):
        """
        Given an os server object return a CB consumable power and task states

        See https://wiki.openstack.org/wiki/VMState for more info
        """
        # power states mapping:
        # 0: NOSTATE,
        # 1: RUNNING,
        # 2: _UNUSED,
        # 3: PAUSED,
        # 4: SHUTDOWN,
        # 5: _UNUSED,
        # 6: CRASHED,
        # 7: SUSPENDED,
        if server is None:
            return "UNKNOWN", "error"

        server_dict = server.to_dict()
        ps = server_dict.get("OS-EXT-STS:power_state")
        vs = server_dict.get("OS-EXT-STS:vm_state")
        ts = server_dict.get("OS-EXT-STS:task_state")
        if vs == "error":
            message = server.fault.get("message")
            return "UNKNOWN", "error", message

        power_status = "UNKNOWN"
        if ps == 1:
            power_status = "POWERON"
        elif ps in [3, 4, 7]:
            power_status = "POWEROFF"

        if not ts:
            # no in progress-api state so get the vm state instead
            ts = vs

        return power_status, ts, None

    def is_task_complete(self, svr_id, task_id):
        """
        Contacts OpenStack to see if the task (ex. creating a VM from image) is
        complete and returns True if so.  If the task is complete, but failed,
        this method will throw a CloudBoltException.

        Returns a 2 item tuple: a boolean for whether the job is done, and
        an integer percentage complete.
        """
        wrapper, server, location_name = self.setup_connection(
            resource_id=svr_id)
        power, task, message = wrapper.get_server_power_and_task_state(server)
        if task == "error":
            raise IllegalStateException(
                f"Provisioning failed - the server is in an error state in "
                f"OpenStack. Error: {message}"
            )
        elif task != "active":
            return False, 50  # there is no real progress here, but it's fast
        else:
            server = Server.objects.get(id=svr_id)
            server.resource_handler_svr_id = task_id
            server.save()
            return True, 100

    TechnologyWrapper.get_server_power_and_task_state = get_server_power_and_task_state
    OpenStackHandler.is_task_complete = is_task_complete