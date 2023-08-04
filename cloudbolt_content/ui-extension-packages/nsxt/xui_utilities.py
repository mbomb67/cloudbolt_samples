import requests
from urllib.parse import urlencode

from accounts.models import Group
from c2_wrapper import create_hook, create_custom_field
from common.methods import set_progress
from network_virtualization.models.network_virtualization_resource_handler_mapping import (
    NetworkVirtualizationResourceHandlerMapping,
)
from network_virtualization.nsx_t.models import NSXTNetworkVirtualization
from network_virtualization.nsx_t.nsxt_wrapper import NSXTAPIWrapper
from orders.models import CustomField, CustomFieldValue
from resources.models import Resource
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


class NSXTXUIAPIWrapper(NSXTAPIWrapper):
    """
    Wrapper for NSX-T API. This class is used to make API calls to the NSX-T
    manager. To get started with this class, you can do the following. First,
    you need to create a ResourceHandler object that is mapped to the
    appropriate NSX-T manager. Then, you can create an instance of this class:
    from xui.nsxt.xui_utilities import NSXTXUIAPIWrapper
    nsx = NSXTXUIAPIWrapper(rh)
    """

    def __init__(self, rh):
        """
        :param rh: ResourceHandler object that is mapped to the appropriate
        NSX-T manager
        """
        self.resource_handler = rh.cast()
        self.sdn = NSXTNetworkVirtualization.objects.filter(
            mappings__resource_handler=self.resource_handler
        ).first()
        if not self.sdn:
            raise Exception(f'No NSX-T manager found for resource handler '
                            f'{rh}')
        super().__init__(
            self.sdn.ip,
            self.sdn.serviceaccount,
            self.sdn.servicepasswd,
            port=self.sdn.port,
            protocol=self.sdn.protocol,
            verify=self.sdn.get_ssl_verification(),
        )

    # Overriding the method from the parent class to use the updated _request
    # method with improved error handling
    def post(self, url, data, content_type="application/json"):
        return self._request("POST", url, data=data, content_type=content_type)

    def patch(self, url, data, content_type="application/json"):
        response = self._request("PATCH", url, data=data,
                                 content_type=content_type)
        return response

    def put(self, url, data, content_type="application/json"):
        return self._request("PUT", url, data=data, content_type=content_type)

    def get(self, url, content_type="application/json"):
        return self._request("GET", url, content_type=content_type)

    def delete(self, url, content_type="application/json"):
        return self._request("DELETE", url, content_type=content_type)

    def get_all_security_tags(self):
        """
        Returns all NSX-T security tags provided from the NSX-T manager
        :return: :list: containing tag str name
        """
        logger.info("Getting NSX security tags")
        res = self.get("/policy/api/v1/infra/tags")
        tags = []
        for item in res["results"]:
            tags.append(item["tag"])
        return tags

    def get_app_tags(self):
        """
        Filters NSX-T security tags provided by get_all_security_tags for tags starting with 'APP'
        :return: List containing tags that start with "APP"
        """
        logger.info("Getting NSX-T APP tags")
        res = self.get("/policy/api/v1/infra/tags")
        tags = []
        for item in res["results"]:
            if item["tag"][:3] == "APP":
                tags.append(item["tag"])
        return tags

    def add_tag_to_vm(self, tag, external_id):
        """
        Add tags from VM
        :param tag: (str) an existing tag
        :param external_id: (str) external_id provided by get_external_id method
        :return: :class:`Response <Response>` object
        """
        url = "/api/v1/fabric/virtual-machines?action=add_tags"
        body = {"external_id": external_id, "tags": [{"tag": tag}]}
        return self.post(url, body)

    def remove_tag_from_vm(self, tag, external_id):
        """
        Remove tags from VM
        :param tag: (str) an existing tag
        :param external_id: (str) external_id provided by get_external_id method
        :return: :class:`Response <Response>` object
        """
        url = "/api/v1/fabric/virtual-machines?action=remove_tags"
        body = {"external_id": external_id, "tags": [{"tag": tag}]}
        return self.post(url, body)

    def get_domain_tags(self):
        """
        Filters NSX-T security tags provided by get_all_security_tags for tags starting with 'DOMAIN'
        :return: List containing tags that start with "DOMAIN"
        """
        logger.info("Getting NSX APP tags")
        res = self.get("/policy/api/v1/infra/tags")

        tags = []
        for item in res["results"]:
            if item["tag"][:6] == "DOMAIN":
                tags.append(item["tag"])
        return tags

    def get_tag_name_by_id(self, tag_id):
        """
        Given a tag ID, returns the name of a tag
        :param tag_id: ID of the tag provided by nsx-t manager
        :return: :str: name of given tag ID
        """
        for t in self.get_all_security_tags():
            if t["tag_id"] == tag_id:
                return t["name"]
        return None

    def get_external_id(self, server):
        """
        Returns the external_id of a given hostname from nsx-t manager
        :param server: Server object that you want the external_id of
        :return: :str: external_id
        """
        res = self.get("/api/v1/fabric/virtual-machines")
        res = res["results"]
        hostname = server.hostname

        # Get the external ID of the machine
        for machine in res:
            if hostname == machine["display_name"]:
                return machine["external_id"]

        # If external_id is not found, then the machine does not exist in nsx-t manager
        logger.error(
            f"Error occured: {hostname} not found in {self._base_url}")
        return None

    def list_infrastructure_groups(self, domain="default"):
        """
        Returns a list of groups from the nsx-t manager
        :param domain: (str) domain to list groups from
        """
        res = self.get(f"/policy/api/v1/infra/domains/{domain}/groups")
        return res

    def get_infrastructure_group(self, group_id, domain="default"):
        """
        Returns a group from the nsx-t manager given a group ID
        :param group_name: (str) name of the group - if none, returns all groups
        """
        res = self.get(f"/policy/api/v1/infra/domains/{domain}/groups/"
                       f"{group_id}")
        return res

    def create_or_update_infrastructure_groups(self, group_id, display_name,
                                               update: bool = False,
                                               expression: list = [],
                                               description="Created by CloudBolt",
                                               domain="default",
                                               **kwargs):
        """
        Creates or updates an infrastructure group in NSX-T. If update is False
        and a group exists with the same groups_id, this request will fail. If
        update is True and a group exists with the same group_id, this request
        will update the existing group.
        :param display_name: (str) name of the group
        :param domain: Domain of the group
        :param expression: Expression used to build the group membership
        :param update: bool - if true, updates the group with the given ID
        :param description: Description of the group
        :param group_id: ID of the group
        """
        url = f"/policy/api/v1/infra/domains/{domain}/groups/{group_id}"
        data = {
            "display_name": display_name,
            "description": description,
            "expression": expression,
        }
        data = {**kwargs, **data}
        if update:
            data["_revision"] = self.get(url)["_revision"]
        res = self.put(url, data)
        return res

    def delete_infrastructure_group(self, group_id, domain="default"):
        """
        Deletes an infrastructure group in NSX-T.
        :param group_id: ID of the group
        :param domain: Domain of the group
        """
        url = f"/policy/api/v1/infra/domains/{domain}/groups/{group_id}"
        res = self.delete(url)
        return res

    def list_distributed_firewall_policies(self, domain="default"):
        """
        Returns a list of distributed firewall policies
        :param domain: Domain of the group
        """
        res = self.get(f'/policy/api/v1/infra/domains/{domain}/'
                       f'security-policies')
        return res

    def get_distributed_firewall_policy(self, policy_id, domain="default"):
        """
        Returns a distributed firewall policy
        :param domain: Domain of the group
        :param policy_id: ID of the policy
        """
        res = self.get(f'/policy/api/v1/infra/domains/{domain}/'
                       f'security-policies/{policy_id}')
        return res

    def create_or_update_distributed_firewall_policy(
            self,
            policy_id,
            display_name,
            scope: list = [],
            category="Application",
            update: bool = False,
            description="Created by CloudBolt",
            domain="default",
            **kwargs
    ):
        """
        Returns the distributed firewall policy from the nsx-t manager
        Create a distributed firewall policy if it does not exist
        :param domain: Domain of the group
        :param policy_id: ID of the policy
        :param display_name: Name of the policy
        :param scope: Scope of the policy
        :param category: Category of the policy
        :param update: bool - if true, updates the policy with the given ID
        :param description: Description of the policy
        :param kwargs: Additional NSXT API arguments
        """
        url = f"/policy/api/v1/infra/domains/{domain}/security-policies/" \
              f"{policy_id}"
        data = {
            "display_name": display_name,
            "description": description,
            "category": category,
            "scope": scope,
        }
        data = {**kwargs, **data}
        if update:
            data["_revision"] = self.get(url)["_revision"]
        res = self.put(url, data)
        return res

    def delete_distributed_firewall_policy(self, policy_id, domain="default",):
        """
        Returns the distributed firewall policy from the nsx-t manager
        Create a distributed firewall policy if it does not exist
        :param domain: Domain of the group
        :param policy_id: ID of the policy
        """
        url = f"/policy/api/v1/infra/domains/{domain}/security-policies/" \
              f"{policy_id}"
        res = self.delete(url)
        return res

    def list_distributed_firewall_rules(self, security_policy_id,
                                        domain="default"):
        """
        Returns a list of distributed firewall rules
        :param security_policy_id: Security policy ID
        :param domain: Domain of the group
        """
        res = self.get(f'/policy/api/v1/infra/domains/{domain}/'
                       f'security-policies/{security_policy_id}/rules')
        return res

    def get_distributed_firewall_rule(self, rule_id, security_policy_id,
                                      domain="default"):
        """
        Returns a distributed firewall policy
        :param security_policy_id: Security policy ID
        :param rule_id: Rule ID
        :param domain: Domain of the group
        """

        res = self.get(f'/policy/api/v1/infra/domains/{domain}/'
                       f'security-policies/{security_policy_id}/rules/'
                       f'{rule_id}')
        return res

    def create_or_update_distributed_firewall_rule(
            self,
            rule_id,
            security_policy_id,
            display_name,
            action,
            source_group_refs: list = [],
            destination_group_refs: list = [],
            services: list = ["ANY"],
            update: bool = False,
            description="Created by CloudBolt",
            domain="default",
            **kwargs
    ):
        """
        Returns the distributed firewall policy from the nsx-t manager
        Create a distributed firewall policy if it does not exist
        :param security_policy_id: Security policy ID
        :param rule_id: Rule ID
        :param display_name: Name of the rule
        :param action: Action of the rule - ALLOW, DROP, REJECT, JUMP_TO_APPLICATION
        :param source_group_refs: Source group URL references
        :param destination_group_refs: Destination group URL references
        :param services: Services
        :param update: bool - if true, updates the group with the given ID
        :param description: Description of the group
        :param domain: Domain of the group
        :param kwargs: Additional NSXT API arguments
        """
        url = f'/policy/api/v1/infra/domains/{domain}/security-policies/' \
              f'{security_policy_id}/rules/{rule_id}'
        data = {
            "display_name": display_name,
            "description": description,
            "action": action,
            "source_groups": source_group_refs,
            "destination_groups": destination_group_refs,
            "services": services,
        }
        data = {**kwargs, **data}
        if update:
            data["_revision"] = self.get(url)["_revision"]
        res = self.put(url, data)
        return res

    def delete_distributed_firewall_rule(self, rule_id, security_policy_id,
                                         domain="default", ):
        """
        Returns the distributed firewall policy from the nsx-t manager
        Create a distributed firewall policy if it does not exist
        :param security_policy_id:
        :param rule_id:
        :param domain: Domain of the group
        """
        url = f'/policy/api/v1/infra/domains/{domain}/security-policies/' \
              f'{security_policy_id}/rules/{rule_id}'
        res = self.delete(url)
        return res

    def create_or_update_expression(self, paths: list, group_id,
                                    domain="default",
                                    expression_id="cloudbolt"):
        """
        Creates or updates an expression for an NSX Group
        :param expression_id: ID of the expression
        :param paths: List of paths to include in the expression
        :param domain: Domain of the group
        :param group_id: ID of the group
        """
        url = f"/policy/api/v1/infra/domains/{domain}/groups/{group_id}/" \
              f"path-expressions/{expression_id}"
        data = {
            "paths": paths,
            "resource_type": "PathExpression"
        }
        response = self.patch(url, data)
        return response

    def update_group_expression(self, expression: list, group_id,
                                domain="default"):
        """
        Updates an expression for an NSX Group
        :param expression: List of criteria to include in the expression
        :param group_id: ID of the NSX Group
        :param domain: Domain of the group
        """
        url = f"/policy/api/v1/infra/domains/{domain}/groups/{group_id}"
        data = {
            "expression": expression
        }
        response = self.patch(url, data)
        return response

    def search(self, query_list: list):
        """
        Search for a resource with a query list. Queries should be passed in as
        a list of strings. For example, to search for a group with the name
        "test", this would be passed in as ["resource_type:Group",
        "display_name:test"]
        :param query_list: List of strings to search for
        """
        query = urlencode({"query": ' AND '.join(query_list)})
        base_url = '/policy/api/v1/search'
        url = f'{base_url}?{query}'
        res = self.get(url)
        return res

    def _request(self, method, url, data=None,
                 content_type="application/json"):
        """
        Overrides the OOB method for the NSXTAPIWrapper class in CloudBolt.
        This allows for better error handling for failed requests
        :param method:
        :param url:
        :param data:
        :param content_type:
        :return:
        """
        headers = {
            "Content-Type": content_type,
            "X-XSRF-TOKEN": self.token,
            "Cookie": self.cookie,
        }
        work_url = f"{self._base_url}{url}"

        response = requests.request(
            method,
            work_url,
            json=data,
            headers=headers,
            verify=self.verify,
            proxies=self.proxies,
        )

        try:
            if content_type == "application/json":
                result = response.json()
            else:
                result = response.text()
        except Exception:
            result = response

        status = response.status_code
        if status not in (
                requests.codes.OK,
                requests.codes.CREATED,
                requests.codes.NO_CONTENT,
        ):
            msg = f"{method} to {url} got unexpected response code: {status}" \
                  f" (content = '{result}')"
            logger.error(msg)
            raise Exception(msg)
        # refresh token and cookie now that we established it's a valid response
        self.token = response.headers.get("X-XSRF-TOKEN", self.token)
        self.cookie = response.headers.get("Set-Cookie", self.cookie)

        return result


# Check a given resource handler for to see if it has an NSX-T object defined
def check_for_nsxt(rh):
    """
    Checks to see if a given ResourceHandler object is associated with an NetworkVirtualiztion connectoin
    :param rh: ResourceHandler object that is mapped to the appropriate the NSX-T manager
    :return: True or False
    """
    try:
        if NetworkVirtualizationResourceHandlerMapping.objects.get(
                resource_handler_id=rh.id
        ):
            return True
    except:
        return False


# Create the required parameters for the NSXT tags
def setup_nsx_tags():
    """
    Generates the required custom_field in CloudBolt CMP

    :return: :class: `CustomField` object
    """
    nsxt_tag_cf = {
        "name": "nsxt_tag",
        "label": "NSX-T Tag",
        "type": "STR",
        "description": "Custom Field for NSX-T tags",
        "show_on_servers": True,
        "show_as_attribute": True,
    }
    tag_cf = CustomField.objects.get_or_create(**nsxt_tag_cf)

    generate_options_for_tag_action = {
        "name": "Generate Options for NSX-T Security Tags",
        "description": (
            "Generates options for NSX-T Security Tags that can be added to any server"
        ),
        "hook_point": "generated_custom_field_options",
        "module": "/var/opt/cloudbolt/proserv/xui/nsxt/generate_options_for_nsxt_tags.py",
        "enabled": True,
        "custom_fields": ["nsxt_tag"],
    }
    create_hook(**generate_options_for_tag_action)
    return tag_cf


def generate_options_for_env_id(field=None, **kwargs):
    group = kwargs.get("group")
    if not group:
        resource = kwargs.get("resource")
        group = resource.group
    if not group:
        logger.error(f"No group found from kwargs: {kwargs}")
        return []
    envs = group.get_available_environments()
    options = [("", "--- Select an Environment ---")]
    for env in envs:
        if env.resource_handler:
            if env.resource_handler.resource_technology:
                if env.resource_handler.resource_technology.name == "VMware vCenter":
                    try:
                        nsx = NSXTNetworkVirtualization.objects.filter(
                            mappings__resource_handler=env.resource_handler
                        ).first()
                        if nsx:
                            get_nsxt_options_from_env(env)
                            set_progress(f'env_id: {env.id}, env_name: {env.name}')
                            options.append((env.id, env.name))
                    except Exception as e:
                        logger.debug(f'Environment did not have nsxt options '
                                     f'set')
    return options


def get_nsxt_options_from_env(env):
    nsxt_tier_1 = get_cfv_for_field("nsxt_tier_1", env)
    nsxt_transport_zone = get_cfv_for_field("nsxt_transport_zone", env)
    return nsxt_transport_zone, nsxt_tier_1


def get_cfv_for_field(field_name, env):
    query_set = env.custom_field_options.filter(field__name=field_name)
    if query_set.count() > 1:
        raise Exception(f"More than one value was found for field: "
                        f"{field_name}")
    if query_set.count() == 0:
        raise Exception(f"No values were found for field: {field_name}")
    return query_set.first().value


def create_field_set_value(resource, name, label, value):
    field = create_custom_field(name, label, "STR", show_as_attribute=True,
                                namespace="nsxt_xui")
    resource.set_value_for_custom_field(name, value)
    return field


def generate_options_for_nsxt_groups(field=None, **kwargs):
    group = kwargs.get('group')
    if group:
        nsxt_groups = get_group_resources_by_type(group, 'nsxt_group')
        options = []
        for nsxt_group in nsxt_groups:
            options.append((nsxt_group.nsxt_group_ref, nsxt_group.name))
        return options


def generate_options_for_nsxt_segments(field=None, **kwargs):
    group = kwargs.get('group')
    nsxt_segments = get_group_resources_by_type(group, 'nsxt_network_segment')
    options = []
    for nsxt_segment in nsxt_segments:
        options.append((nsxt_segment.nsxt_segment_ref, nsxt_segment.name))
    return options


def get_group_resources_by_type(group, resource_type):
    resources = Resource.objects.filter(
        group=group,
        resource_type__name=resource_type,
        lifecycle='ACTIVE')
    return resources


def get_cf_values(resource, cf_name):
    cfvs = resource.get_cfvs_for_custom_field(cf_name)
    values = []
    for cfv in cfvs:
        values.append(cfv.value)
    return values


def update_expression_parameters(resource, nsxt_groups: list=None,
                                 nsxt_segments: list=None, ip_addresses=[],
                                 mac_addresses=[]):
    cfvm = resource.get_cfv_manager()
    # Remove original values
    for field in ["nsxt_group_refs", "nsxt_segment_refs", "ip_addresses",
                  "mac_addresses"]:
        values = cfvm.filter(field__name=field)
        for value in values:
            cfvm.remove(value)
    # Add new values
    for nsxt_group in nsxt_groups:
        create_cfv_add_to_list(cfvm, "nsxt_group_refs", nsxt_group)
    for nsxt_segment in nsxt_segments:
        create_cfv_add_to_list(cfvm, "nsxt_segment_refs", nsxt_segment)
    for ip_address in ip_addresses:
        create_cfv_add_to_list(cfvm, "nsxt_ip_addresses", ip_address)
    for mac_address in mac_addresses:
        create_cfv_add_to_list(cfvm, "nsxt_mac_addresses", mac_address)


def create_cfv_add_to_list(cfvm, cf_name, value):
    cf = CustomField.objects.get(name=cf_name)
    cfv, _ = CustomFieldValue.objects.get_or_create(field=cf, value=value)
    cfvm.add(cfv)
