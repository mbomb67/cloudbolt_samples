#!/usr/bin/env python
"""
Provided in this module are the 6 signature methods that define interactions with Infoblox.
"""
import time
import json

from infrastructure.models import Server
from utilities.exceptions import CloudBoltException
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)
NETWORK_VIEW = 'default'


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
        {
            "name": host_fqdn,
            "network_view": NETWORK_VIEW,
        }
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
    wrapper.BASE_URL = f'https://{wrapper.BASE_URL.split("/")[2]}/wapi/v2.0/'
    # At this point, the server should be in the PROV state.
    server = Server.objects.get(hostname=hostname, status="PROV")
    ip_address = server.sc_nic_0_ip
    host_record = wrapper.get_host_by_ip(ip_address)

    # Delete the original record - InfoBlox does not allow updating a fixed addr
    if host_record:
        ref = host_record[0]["_ref"]
        wrapper.delete_fixed_address(ref)

    #Add a fixed record with the correct MAC address
    response = wrapper.add_fixed_record(
        host_record[0]["ip_address"],
        {
            "name": host_record[0]["names"][0],
            "network_view": host_record[0]["network_view"],
            "network": host_record[0]["network"],
            "mac": mac_address,
        }
    )
    return response


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
