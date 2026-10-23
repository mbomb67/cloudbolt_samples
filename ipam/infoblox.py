"""
Provided in this module are the 6 signature methods that define interactions with Infoblox.

Drop-in replacement for CloudBolt's out-of-the-box Infoblox IPAM orchestration
(cbhooks/hookmodules/ipam/infoblox.py). Paste it into Admin > IP Address
Management > <Infoblox instance> > Orchestration; the edit applies only to that
instance and is kept across CloudBolt upgrades.

Why: the OOTB module finds host records with the wrapper's get_host_by_name(),
which runs an unanchored, case-sensitive regex search (record:host?name~=) and
takes the first result. With an uppercase hostname (e.g. CBTEST1044) the record
is created, the lookup that follows finds nothing, allocate_ip() returns None,
sc_nic_0_ip stays empty and the build goes out with no IP. Because the search is
unanchored, a lowercase name can also match a different, longer one (cbtest104
matches cbtest1044.example.com), so the wrong IP could be returned, the wrong
host's record updated for DHCP, or the wrong record deleted.

Changes from the OOTB module:
  - the FQDN is lowercased before the record is created, so the stored name
    does not depend on how a given Infoblox grid normalizes case
  - every lookup goes through _find_host_record(), which only accepts a
    record whose name matches exactly (case-insensitive)
  - allocate_ip() fails the build if it can't read back the record it just
    created, instead of returning no IP
  - setup_dhcp_for_host() and delete_host() act on the exact record found,
    by its _ref, rather than on the wrapper's first regex match / "default"
    DNS view
"""
import json

from utilities.exceptions import CloudBoltException


def _find_host_record(wrapper, name):
    """
    Return the host record whose name exactly matches `name`, or None.

    `name` may be an FQDN or a bare hostname; a bare hostname is compared with
    the first label of each record's name. Comparison is case-insensitive.
    """
    name = name.lower()
    url = "{}record:host?name~={}".format(wrapper.BASE_URL, name)
    for record in wrapper._run_get_command(url):
        record_name = record.get("name", "").lower()
        if "." not in name:
            record_name = record_name.split(".", 1)[0]
        if record_name == name:
            return record
    return None


def is_hostname_valid(infoblox, server, network=None):
    """
    Call out to Infoblox to verify that a given hostname is not already in use.

    Code defined here will be executed at the Pre-Create Resource trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    host = _find_host_record(wrapper, server.hostname)
    return host is None


def get_dns_domain(server, network):
    dns_domain = server.get_value_for_custom_field("dns_domain") or ""
    if not dns_domain:
        # fall back to the value on the network
        dns_domain = network.dns_domain
    return dns_domain


def allocate_ip(infoblox, server, network):
    """
    Call out to Infoblox to allocate an IP address for a given hostname.
    The return value from this function can be 'dhcp'.

    Code defined here will be executed at the Pre-Create Resource trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    dns_domain = get_dns_domain(server, network)
    host_fqdn = f"{server.hostname}.{dns_domain}".lower()
    wrapper.add_host_record(
        host_fqdn, "func:nextavailableip:{}".format(network.ipam_network.network_ref)
    )

    host_record = _find_host_record(wrapper, host_fqdn)
    if not host_record or not host_record.get("ipv4addrs"):
        raise CloudBoltException(
            "Created host record '{}' in IPAM '{}' but could not read it back to get "
            "its IP. The record may need to be removed from Infoblox manually.".format(
                host_fqdn, infoblox.name
            )
        )
    return host_record["ipv4addrs"][0]["ipv4addr"]


def setup_dhcp_for_host(infoblox, hostname, mac_address):
    """
    Code defined here will be executed at the Pre-Network Configuration trigger point.
    """
    wrapper = infoblox.get_api_wrapper()
    host_record = _find_host_record(wrapper, hostname)
    if not host_record:
        raise CloudBoltException(
            "Host '{}' not found in IPAM '{}'".format(hostname, infoblox.name)
        )
    url = "{}{}".format(wrapper.BASE_URL, host_record["ipv4addrs"][0]["_ref"])
    body = {"mac": "{}".format(mac_address), "configure_for_dhcp": True}
    wrapper._run_put_command(url, json.dumps(body))


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
    # host_fqdn may be the fully qualified domain name or just the hostname.
    info = _find_host_record(wrapper, host_fqdn)
    if not info:
        raise CloudBoltException(
            "Error getting host '{}' from IPAM '{}'".format(host_fqdn, infoblox.name)
        )
    # delete_fixed_address() is a plain DELETE by object reference, so it works
    # for host records too, in whatever DNS view the record lives.
    wrapper.delete_fixed_address(info["_ref"])
