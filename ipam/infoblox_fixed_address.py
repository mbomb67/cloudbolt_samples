#!/usr/bin/env python
"""
Provided in this module are the 6 signature methods that define interactions with Infoblox.
"""
import time

from infrastructure.models import Server
from utilities.exceptions import CloudBoltException
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def is_hostname_valid(infoblox, server, network=None):
    """
    Call out to Infoblox to verify that a given hostname is not already in use.

    Code defined here will be executed at the Pre-Create Resource trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    host = wrapper.get_host_by_name(server.hostname)
    return host is None


def allocate_ip(infoblox, server, network):
    """
    Call out to Infoblox to allocate an IP address for a given hostname.
    The return value from this function can be 'dhcp'.

    Code defined here will be executed at the Pre-Create Resource trigger point.
    """
    ip = None
    wrapper = infoblox.get_api_wrapper()
    wrapper.BASE_URL = f'https://{wrapper.BASE_URL.split("/")[2]}/wapi/v2.0/'
    host_fqdn = server.hostname
    if network.dns_domain:
        host_fqdn = f'{host_fqdn}.{network.dns_domain}'

    """
    wrapper.add_host_record(
        host_fqdn, "func:nextavailableip:{}".format(network.ipam_network.network_ref)
    )
    """
    response = wrapper.add_fixed_record(
        f'func:nextavailableip:{network.ipam_network.network_ref}',
        {"name": host_fqdn}
    )
    response_json = response.json()
    ip = response_json["ipv4addr"]
    server.sc_nic_0_ip = ip
    server.save()
    return ip


def setup_dhcp_for_host(infoblox, hostname, mac_address):
    """
    Code defined here will be executed at the Pre-Network Configuration trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    wrapper.setup_dhcp_for_host(hostname, mac_address)


def restart_dhcp_service(infoblox):
    """
    Code defined here will be executed at the Pre-Network Configuration trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    wrapper.restart_dhcp_service()


def delete_host(infoblox, host_fqdn, network=None):
    """
    Call out to Infoblox to remove a fixed record and free up that IP on the
    network.

    Code defined here will be executed at the Post-Decomission trigger point.
    """
    # Due to constraints in the way we call InfoBlox record deletions, this
    # Step will be handled with a Pre-Delete Orchestration Action.
    return None
