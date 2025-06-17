"""
A CloudBolt pre-delete script for Infoblox IPAM. Will delete all Fixed Records
from InfoBlox for the server in question.
"""
from common.methods import set_progress
from ipam.infoblox.models import InfobloxIPAM
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)
NETWORK_VIEW = 'default'

def run(job, server=None, *args, **kwargs):
    set_progress(f"Running Infoblox pre-delete script for server "
                 f"{server.hostname}")
    nics = server.nics.all()
    # Loop through each NIC on the VM. If the NIC has an IP, check if it is
    # managed by Infoblox. If it is, delete the Fixed Record from Infoblox.
    for nic in nics:
        network = nic.network
        if not network:
            logger.debug(f"NIC {nic} does not have a network. Skipping.")
            continue
        if not network.ipam_network:
            logger.debug(f"IPAM for network {network} is not Infoblox. "
                         f"Skipping nic {nic}")
            continue
        infoblox = network.ipam_network.ipam
        if type(infoblox) != InfobloxIPAM:
            logger.debug(f"IPAM for network {network} is not Infoblox. "
                         f"Skipping nic {nic}")
            continue
        if nic.ip:
            nic_ip = nic.ip
            wrapper = infoblox.get_api_wrapper()
            wrapper.BASE_URL = (f'https://{wrapper.BASE_URL.split("/")[2]}/wapi'
                                f'/v2.0/')
            infos = wrapper.get_fixed_address_records(nic_ip, NETWORK_VIEW)
            if len(infos) > 1:
                raise Exception(f"Multiple IP records found for IP {nic_ip}. "
                                f"Skipping.")
            info = infos[0] if len(infos) == 1 else None
            # Generate the hostname that should be associated with this IP
            host_fqdn = server.hostname
            if network.dns_domain:
                host_fqdn = f'{host_fqdn}.{network.dns_domain}'

            if not info:
                logger.debug(f"IP: {nic_ip} for FQDN: '{host_fqdn}' not found "
                             f"in IPAM {infoblox.name}', skipping.")
                continue
            if info["name"] == host_fqdn:
                ip_ref = info.get("_ref", None)
                if not ip_ref:
                    logger.debug(f"IP REF for '{host_fqdn}' not found in IPAM "
                                 f"'{infoblox.name}'")
                    continue
                set_progress(f"Deleting IP address: '{nic_ip}' from InfoBlox")
                wrapper.delete_fixed_address(ip_ref)
                logger.debug(f"Deleted host '{host_fqdn}' from Network "
                             f"'{network}'")
                return
            else:
                logger.debug(f"Hostname associated with IP record didn't match "
                             f"expected hostname. Expected: '{host_fqdn}', "
                             f"Actual: '{info['name']}'")
                continue
        else:
            logger.debug(f"NIC {nic} does not have an IP address. Skipping.")
            continue


    return "", "", ""