"""
Provided in this module are the 6 signature methods that define interactions with Infoblox.

Drop-in replacement for CloudBolt's out-of-the-box Infoblox IPAM orchestration
(cbhooks/hookmodules/ipam/infoblox.py). Paste it into Admin > IP Address
Management > <Infoblox instance> > Orchestration; the edit applies only to that
instance and is kept across CloudBolt upgrades.

Why: Infoblox stores host record names in lowercase, but the wrapper's
get_host_by_name() searches with a case-sensitive regex (record:host?name~=).
With an uppercase hostname (e.g. CBTEST1044) the record is created, but the
lookup that follows finds nothing, so allocate_ip() returns None, sc_nic_0_ip
stays empty, and the build goes out with no IP. The same lookup also breaks the
duplicate check, DHCP setup, and decommission for uppercase names.

Changes from the OOTB module:
  - every name passed to get_host_by_name() (directly or via the wrapper's
    setup_dhcp_for_host()) is lowercased
  - allocate_ip() looks the record up by its FQDN instead of the bare
    hostname, so the unanchored regex can't match a longer name such as
    cbtest1044 when looking up cbtest104
"""

from utilities.exceptions import CloudBoltException


def is_hostname_valid(infoblox, server, network=None):
    """
    Call out to Infoblox to verify that a given hostname is not already in use.

    Code defined here will be executed at the Pre-Create Resource trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    host = wrapper.get_host_by_name(server.hostname.lower())
    return host is None


def allocate_ip(infoblox, server, network):
    """
    Call out to Infoblox to allocate an IP address for a given hostname.
    The return value from this function can be 'dhcp'.

    Code defined here will be executed at the Pre-Create Resource trigger point.
    """
    ip = None
    wrapper = infoblox.get_api_wrapper()
    host_fqdn = "{}.{}".format(server.hostname, network.dns_domain)
    wrapper.add_host_record(
        host_fqdn, "func:nextavailableip:{}".format(network.ipam_network.network_ref)
    )

    host_record = wrapper.get_host_by_name(host_fqdn.lower())
    if host_record:
        host_ipv4addrs = host_record["ipv4addrs"]
        ip = host_ipv4addrs[0]["ipv4addr"]

    return ip


def setup_dhcp_for_host(infoblox, hostname, mac_address):
    """
    Code defined here will be executed at the Pre-Network Configuration trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    wrapper.setup_dhcp_for_host(hostname.lower(), mac_address)


def restart_dhcp_service(infoblox):
    """
    Code defined here will be executed at the Pre-Network Configuration trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    wrapper.restart_dhcp_service()


def delete_host(infoblox, host_fqdn, network=None):
    """
    Call out to Infoblox to remove a host record and free up that hostname/IP.

    Code defined here will be executed at the Post-Decomission trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    # This will get the host info, including FQDN, given either fully
    # qualified domain name or just hostname without the domain.
    info = wrapper.get_host_by_name(host_fqdn.lower())
    # We're expecting info to contain a dictionary but should confirm.
    if not isinstance(info, dict):
        raise CloudBoltException(
            "Error getting host '{}' from IPAM '{}'".format(host_fqdn, infoblox.name)
        )
    host_fqdn = info.get("name", None)
    if not host_fqdn:
        raise CloudBoltException(
            "FQDN '{}' not found in IPAM '{}'".format(host_fqdn, infoblox.name)
        )
    wrapper.delete_host_record(host_fqdn)
