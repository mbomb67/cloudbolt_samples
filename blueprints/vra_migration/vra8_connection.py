"""
This module contains the VRealizeAutomation8Connection class which is used to
interact with the VRA8 API.

Usage:
    vra = VRealizeAutomation8Connection(CONN_INFO_ID)
"""
from urllib.parse import urlencode

import requests
import yaml

from utilities.logger import ThreadLogger
from utilities.models import ConnectionInfo

logger = ThreadLogger(__name__)


VERIFY_CERTS = False


def generate_options_for_vra_projects(field, control_value=None, **kwargs):
    if not control_value:
        return [("", "------First, Select the vRA Connection------")]
    vra = VRealizeAutomation8Connection(control_value)
    return vra.get_project_options()


def generate_options_for_vra_connection(**kwargs):
    cis = ConnectionInfo.objects.filter(labels__name="vra8")
    return [(ci.id, ci.name) for ci in cis]


class VRealizeAutomation8Connection(object):
    def __init__(self, conn_info_id):
        self.conn_info_id = conn_info_id
        self.conn_info = self.get_connection_info()
        self.base_url = self.get_base_url()
        self.headers = self.get_headers()

    def get_vm_from_instance_uuid(self, instance_uuid):
        url = (f"/iaas/api/machines?$filter="
               f"customProperties.instanceUUID%20eq%20'{instance_uuid}'")
        response_json = self.submit_request(url)
        if response_json["numberOfElements"] != 1:
            raise Exception(f'{response_json["numberOfElements"]} results found'
                            f' for instance_uuid: {instance_uuid}')
        return response_json["content"][0]

    def get_project_name(self, project_id):
        return self.get_project(project_id)["name"]

    def get_project(self, project_id):
        url = f'/iaas/api/projects/{project_id}'
        response_json = self.submit_request(url)
        return response_json

    def list_projects(self, query_params: dict = {}):
        url = f'/iaas/api/projects'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_project_options(self):
        results = 200
        skip = 0
        found_results = 200
        options = []
        while found_results != 0:
            projects = self.list_projects({"$top": results, "$skip": skip})
            for project in projects["content"]:
                options.append((project["id"], project["name"]))
            skip = skip + results
            found_results = projects["numberOfElements"]
        return options

    def list_resources(self, query_params: dict = {}):
        url = f'/deployment/api/resources'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_resource(self, resource_id):
        url = f'/deployment/api/resources/{resource_id}'
        response_json = self.submit_request(url)
        return response_json

    def get_resource_from_instance_uuid(self, instance_uuid, vm_name):
        response_json = self.list_resources({"search": vm_name})
        for resource in response_json["content"]:
            try:
                if resource["properties"]["instanceUUID"] == instance_uuid:
                    return resource
            except KeyError:
                pass
        logger.warning(f'No resource found for instance_uuid: {instance_uuid}')
        return None

    def list_deployments(self, query_params: dict = {}):
        """
        Returns a list of deployments. The query_params dict can be used to
        filter the results. For example, to only get deployments for a
        specific project, use the following query_params:
        {"projects": project_id}
        This will only return deployments with a status of CREATE_SUCCESSFUL.
        This can be overridden if needed
        """
        url = f'/deployment/api/deployments'
        if not query_params.get("status", None):
            query_params["status"] = "CREATE_SUCCESSFUL"
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def list_deployment_ids_for_blueprint(self, blueprint_id, project_ids=[]):
        """
        Returns a list of deployment_ids for a given blueprint_id. If a
        project_id is provided, only deployments for that project will be
        returned.
        """
        results = 200
        skip = 0
        found_results = 200
        deployment_ids = []
        while found_results != 0:
            params = {"$top": results, "$skip": skip,
                      "status": "CREATE_SUCCESSFUL"}
            if project_ids: # Only get deployments for this project
                params["projects"] = ','.join(project_ids)
            deployments = self.list_deployments(params)
            for deployment in deployments["content"]:
                if deployment["blueprintId"] == blueprint_id:
                    deployment_ids.append(deployment["id"])
            skip = skip + results
            found_results = deployments["numberOfElements"]
        return deployment_ids

    def get_deployment(self, deployment_id):
        url = f'/deployment/api/deployments/{deployment_id}'
        query_params = {"expand": "resources"}
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_deployments_for_project(self, project_id):
        url = f'/deployment/api/deployments'
        return self.list_deployments({"projects": project_id})

    def get_resources_for_deployment(self, deployment_id):
        url = f'/deployment/api/deployments/{deployment_id}/resources'
        response_json = self.submit_request(url)
        return response_json

    def get_resources_for_project(self, project_id):
        url = f'/deployment/api/resources'
        return self.list_resources({"projects": project_id})

    def list_projects(self, query_params: dict = {}):
        url = f'/iaas/api/projects'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_project(self, project_id):
        url = f'/iaas/api/projects/{project_id}'
        response_json = self.submit_request(url)
        return response_json

    def list_blueprints(self, query_params: dict = {}):
        url = f'/blueprint/api/blueprints'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint(self, blueprint_id):
        url = f'/blueprint/api/blueprints/{blueprint_id}'
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint_versions(self, blueprint_id):
        url = f'/blueprint/api/blueprints/{blueprint_id}/versions'
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint_version(self, blueprint_id, version):
        url = f'/blueprint/api/blueprints/{blueprint_id}/versions/{version}'
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint_version_content(self, blueprint_id, version):
        url = (f'/blueprint/api/blueprints/{blueprint_id}/versions/{version}')
        response_json = self.submit_request(url)
        return response_json["content"]

    def get_blueprint_content(self, blueprint_id):
        """
        Returns the content for the latest version of the blueprint
        """
        url = (f'/blueprint/api/blueprints/{blueprint_id}')
        response_json = self.submit_request(url)
        return response_json["content"]

    def get_blueprint_content_as_dict(self, blueprint_id, version=None):
        """
        Returns the content for the latest version of the blueprint as a dict
        - version: If provided, the content for the specified version will be
            returned
        """
        if version:
            vra_content = self.get_blueprint_version_content(blueprint_id,
                                                             version)
        else:
            vra_content = self.get_blueprint_content(blueprint_id)
        return yaml.load(vra_content, Loader=yaml.FullLoader)


    def list_iaas_machines(self, query_params: dict = {}):
        url = f'/iaas/api/machines'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_iaas_machine(self, machine_id):
        url = f'/iaas/api/machines/{machine_id}'
        response_json = self.submit_request(url)
        return response_json

    def get_iaas_machine_from_instance_uuid(self, instance_uuid):
        response_json = self.list_iaas_machines(
            {"$filter": f'customProperties.instanceUUID eq {instance_uuid}'}
        )
        if response_json["numberOfElements"] != 1:
            raise Exception(f'{response_json["numberOfElements"]} results found'
                            f' for instance_uuid: {instance_uuid}')
        return response_json["content"][0]

    def add_query_params_to_url(self, url, query_params: dict):
        if query_params:
            url += f'?{urlencode(query_params)}'
        return url

    def get_headers(self):
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json'}
        token = self.get_token(headers)
        headers['Authorization'] = f'Bearer {token}'
        return headers

    def get_base_url(self):
        conn = self.conn_info
        base_url = f'{conn.protocol}://{conn.ip}'
        if conn.port:
            base_url += f':{conn.port}'
        return base_url

    def get_connection_info(self):
        conn = ConnectionInfo.objects.get(id=self.conn_info_id)
        assert isinstance(conn, ConnectionInfo)
        return conn

    def get_user_by_username(self, username, org_id):
        url = (f"/csp/gateway/am/api/orgs/{org_id}/users/search"
               f"?userSearchTerm={username}")
        response_json = self.submit_request(url)
        if len(response_json["results"]) != 1:
            raise Exception(f'{len(response_json["results"])} results found'
                            f' for username: {username}')
        return response_json["results"][0]["user"]

    def get_token(self, headers):
        conn = self.conn_info
        base_url = self.base_url
        url = f'{base_url}/csp/gateway/am/api/login'
        username = conn.username.split('@')[0]
        password = conn.password
        if conn.username.find('@') != -1:
            domain = conn.username.split('@')[1]
        else:
            domain = None
        data = {"username": username, "password": password}
        if domain:
            data["domain"] = domain
        response = requests.post(url, headers=headers, verify=VERIFY_CERTS,
                                 json=data)
        response.raise_for_status()
        return response.json()['cspAuthToken']

    def submit_request(self, url_path: str, method: str = "get", **kwargs):
        headers = self.headers
        base_url = self.base_url
        url = f'{base_url}{url_path}'
        if method == "get":
            response = requests.get(url, headers=headers,
                                    verify=VERIFY_CERTS)
        if method == 'post':
            response = requests.post(url, headers=headers,
                                     verify=VERIFY_CERTS, **kwargs)
        try:
            response.raise_for_status()
        except Exception as e:
            logger.error(f'Error encountered for URL: {url}, details: '
                         f'{e.response.content}')
            raise
        return response.json()
