"""
This Shared Module contains methods for connection to the BT Diamond IP Manager

Consume this Shared Module in other CloudBolt Plugins by running the following:
from shared_modules.diamond_ip import RestConnection
with RestConnection(conn_info_id) as conn:
    # Use conn to make requests to the REST API
    response = conn.get('/some_endpoint')
    # response is a JSON object that can be used in your plugin
"""
# Common imports for Integrations below:
from common.methods import set_progress
from utilities.models import ConnectionInfo
from requests import Session
import json
import time
import base64
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

# Global default variables can be set here
VERIFY_CERTS = False


class RestConnection(Session):
    """
    Wrapper for connecting to a generic REST API.
    """

    def __init__(self, conn_info_id):
        """
        :param conn_info_id: The ID of the ConnectionInfo object to use
        """
        # Initialize the Session object
        super(RestConnection, self).__init__()
        # Connection Info could also be referenced by name if needed
        self.conn_info = ConnectionInfo.objects.get(id=conn_info_id)
        self.root_url = f'{self.conn_info.protocol}://{self.conn_info.ip}'
        if self.conn_info.port:
            self.root_url = f'{self.root_url}:{self.conn_info.port}'
        # BT Diamond Base URL for the API
        self.base_url = f'{self.root_url}/inc-rest/api/v1/'
        self.verify = VERIFY_CERTS
        self.headers = {}

    def __enter__(self):
        self.set_headers()
        return self

    def __exit__(self, *args):
        self.close()

    def set_headers(self):
        """
        Get the headers for the REST API
        """
        self.headers.update({
            'Content-Type': 'application/json',
            'Accept': 'application/json'
        })
        # More commonly, basic auth is used to get a short term token used for a
        # session:
        token = self.get_token()
        self.headers.update({'Authorization': f'Bearer {token}'})
        return

    def get_token(self):
        """
        Example method for getting a token from the REST API
        """
        headers = {'Content-Type': 'application/x-www-form-urlencoded'}
        url = f'{self.base_url}/login'
        data = {
            'username': self.conn_info.username,
            'password': self.conn_info.password
        }
        response = self.post(url, data)
        # Update the response below to pull the token from the correct key
        return response.get('token', None)

    def allocate_ip(self, hostname, network, domain_name):
        """
        Allocate an IP address in the BT Diamond IP Manager
        :param hostname: str: The hostname to allocate
        :param network: str: The network to allocate the IP address from
        :param domain_name: str: The domain name to allocate the IP address in
        :return: dict: The response from the API
        """
        url = f"Imports/importDevice"
        data = {
            "inpDevice": {
                "addressType": "Static",
                "hwType": "",
                "description": "This record was provisioned by CloudBolt",
                "deviceType": "Server",
                "domainName": domain_name,
                "hostname": hostname,
                "resourceRecordFlag": "true",
                "MACAddress": "",
                "ipAddress": f"{network}/from-end"
            }
        }
        logger.debug(f'Allocating IP address: {hostname} in network: {network}')
        response = self.submit_request(url, "post", json=data)
        return response.json()

    def release_ip(self, ip_address):
        """
        Rollback an IP address allocation in the BT Diamond IP Manager
        :param allocation_result: dict: The result of the allocation
        :return: dict: The response from the API
        """
        url = "Deletes/deleteDevice"
        data = {
            "inpDev": {
                "ipAddress": ip_address
            }
        }
        logger.debug(f'Deleting IP address: {ip_address}')
        response = self.submit_request(url, "delete", json=data)
        return response.json()

    def submit_request(self, url_path: str, method: str = "get", **kwargs):
        """
        Submit a request to the BT Diamond API with standardized error handling.
        :param url_path:
        :param method:
        :param kwargs:
        :return: dict: The JSON response from the API
        """
        url = f"{self.base_url}{url_path}"
        if method == "get":
            response = self.get(url, **kwargs)
        elif method == "post":
            response = self.post(url, **kwargs)
        elif method == "put":
            response = self.put(url, **kwargs)
        elif method == "delete":
            response = self.delete(url, **kwargs)
        else:
            raise Exception(f"Invalid method: {method}")
        try:
            response.raise_for_status()
        except Exception as e:
            logger.debug(f'Error encountered for URL: {url}, details: '
                         f'{e.response.content}')
            raise
        return response.json()
