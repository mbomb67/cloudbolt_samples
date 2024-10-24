"""
This module contains the AriaAutomation8Connection and
AriaOrchestratorConnection classes, which are used to interact with the VRA8/
Aria API.

Usage:
Create a ConnectionInfo object with either 'vra8' or 'aria' as the label
To connect to vRA for vRA API Calls:
    vra = AriaAutomation8Connection(CONN_INFO_ID)
    projects = vra.list_projects()
To connect to a vRO instance for vRO API Calls:
    vro = AriaOrchestratorConnection(CONN_INFO_ID)
    workflows = vra.list_workflows()
"""
import json
import time
from urllib.parse import urlencode

import requests
import yaml
from django.db.models import Q

from common.methods import set_progress
from utilities.logger import ThreadLogger
from utilities.models import ConnectionInfo

logger = ThreadLogger(__name__)

VERIFY_CERTS = False


def generate_options_for_aria_projects(field, control_value=None, **kwargs):
    if not control_value:
        return [("", "------First, Select the Aria Connection------")]
    vra = AriaAutomationConnection(control_value)
    return vra.get_project_options()


def generate_options_for_aria_connection(field, **kwargs):
    cis = ConnectionInfo.objects.filter(Q(labels__name="vra8") |
                                        Q(labels__name="aria"))
    return [(ci.id, ci.name) for ci in cis]


class AriaAutomationConnection(object):
    def __init__(self, conn_info_id):
        self.conn_info_id = conn_info_id
        self.conn_info = self.get_connection_info()
        self.base_url = self.get_base_url()
        self.headers = self.get_headers()

    def get_vm_from_instance_uuid(self, instance_uuid):
        """
        Returns the Aria VM object for the provided instance_uuid
        :param instance_uuid:
        :return:
        """
        url = (f"/iaas/api/machines?$filter="
               f"customProperties.instanceUUID%20eq%20'{instance_uuid}'")
        response_json = self.submit_request(url)
        if response_json["numberOfElements"] != 1:
            raise Exception(f'{response_json["numberOfElements"]} results found'
                            f' for instance_uuid: {instance_uuid}')
        return response_json["content"][0]

    def get_project_name(self, project_id):
        """
        Returns the name of the project for the provided project_id
        :param project_id:
        :return:
        """
        return self.get_project(project_id)["name"]

    def get_project(self, project_id):
        """
        Returns the project object for the provided project_id
        :param project_id:
        :return:
        """
        url = f'/iaas/api/projects/{project_id}'
        response_json = self.submit_request(url)
        return response_json

    def list_projects(self, query_params=None):
        """
        Returns a list of projects. The query_params dict can be used to
        filter the results.
        :param query_params:
        :return:
        """
        if query_params is None:
            query_params = {}
        url = f'/iaas/api/projects'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_project_options(self):
        """
        Returns a list of tuples containing the project id and name to be
        used in a generated_options_for field
        :return:
        """
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

    def list_resources(self, query_params=None):
        """
        Returns a list of resources. The query_params dict can be used to
        filter the results.
        :param query_params:
        :return:
        """
        if query_params is None:
            query_params = {}
        url = f'/deployment/api/resources'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_resource(self, resource_id):
        """
        Returns the resource object for the provided resource_id
        :param resource_id:
        :return:
        """
        url = f'/deployment/api/resources/{resource_id}'
        response_json = self.submit_request(url)
        return response_json

    def get_resource_from_instance_uuid(self, instance_uuid, vm_name):
        """
        Returns the resource object for the provided instance_uuid and vm_name
        :param instance_uuid:
        :param vm_name:
        :return:
        """
        response_json = self.list_resources({"search": vm_name})
        for resource in response_json["content"]:
            try:
                if resource["properties"]["instanceUUID"] == instance_uuid:
                    return resource
            except KeyError:
                pass
        logger.warning(f'No resource found for instance_uuid: {instance_uuid}')
        return None

    def list_deployments(self, query_params=None):
        """
        Returns a list of deployments. The query_params dict can be used to
        filter the results. For example, to only get deployments for a
        specific project, use the following query_params:
        {"projects": project_id}
        This will only return deployments with a status of CREATE_SUCCESSFUL.
        This can be overridden if needed
        """
        if query_params is None:
            query_params = {}
        url = f'/deployment/api/deployments'
        if not query_params.get("status", None):
            query_params["status"] = "CREATE_SUCCESSFUL"
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def list_deployment_ids_for_blueprint(self, blueprint_id, project_ids=None):
        """
        Returns a list of deployment_ids for a given blueprint_id. If a
        project_id is provided, only deployments for that project will be
        returned.
        """
        if project_ids is None:
            project_ids = []
        results = 200
        skip = 0
        found_results = 200
        deployment_ids = []
        while found_results != 0:
            params = {"$top": results, "$skip": skip,
                      "status": "CREATE_SUCCESSFUL"}
            if project_ids:  # Only get deployments for this project
                params["projects"] = ','.join(project_ids)
            deployments = self.list_deployments(params)
            for deployment in deployments["content"]:
                if deployment["blueprintId"] == blueprint_id:
                    deployment_ids.append(deployment["id"])
            skip = skip + results
            found_results = deployments["numberOfElements"]
        return deployment_ids

    def get_deployment(self, deployment_id):
        """
        Returns the deployment object for the provided deployment_id
        :param deployment_id: 
        :return: 
        """
        url = f'/deployment/api/deployments/{deployment_id}'
        query_params = {"expand": "resources"}
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_deployments_for_project(self, project_id):
        """
        Returns a list of deployments for the provided project_id
        :param project_id:
        :return:
        """
        return self.list_deployments({"projects": project_id})

    def get_resources_for_deployment(self, deployment_id):
        """
        Returns a list of resources for the provided deployment_id
        :param deployment_id:
        :return:
        """
        url = f'/deployment/api/deployments/{deployment_id}/resources'
        response_json = self.submit_request(url)
        return response_json

    def get_resources_for_project(self, project_id):
        """
        Returns a list of resources for the provided project_id
        :param project_id:
        :return:
        """
        return self.list_resources({"projects": project_id})

    def list_blueprints(self, query_params=None):
        """
        Returns a list of blueprints. The query_params dict can be used to
        filter the results.
        :param query_params:
        :return:
        """
        if query_params is None:
            query_params = {}
        url = f'/blueprint/api/blueprints'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint(self, blueprint_id):
        """
        Returns the blueprint object for the provided blueprint_id
        :param blueprint_id:
        :return:
        """
        url = f'/blueprint/api/blueprints/{blueprint_id}'
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint_versions(self, blueprint_id):
        """
        Returns a list of versions for the provided blueprint_id
        :param blueprint_id:
        :return:
        """
        url = f'/blueprint/api/blueprints/{blueprint_id}/versions'
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint_version(self, blueprint_id, version):
        """
        Returns the blueprint version object for the provided blueprint_id and
        version
        :param blueprint_id:
        :param version:
        :return:
        """
        url = f'/blueprint/api/blueprints/{blueprint_id}/versions/{version}'
        response_json = self.submit_request(url)
        return response_json

    def get_blueprint_version_content(self, blueprint_id, version):
        """
        Returns the content for the provided blueprint_id and version
        :param blueprint_id:
        :param version:
        :return:
        """
        url = (f'/blueprint/api/blueprints/{blueprint_id}/versions/{version}')
        response_json = self.submit_request(url)
        return response_json["content"]

    def get_blueprint_content(self, blueprint_id):
        """
        Returns the content for the latest version of the blueprint
        :param blueprint_id:
        :return:
        """
        url = (f'/blueprint/api/blueprints/{blueprint_id}')
        response_json = self.submit_request(url)
        return response_json["content"]

    def get_blueprint_content_as_dict(self, blueprint_id, version=None):
        """
        Returns the content for the latest version of the blueprint as a dict
        :param blueprint_id:
        :param version: If provided, the content for the specified version will be
            returned
        :return: dict
        """
        if version:
            vra_content = self.get_blueprint_version_content(blueprint_id,
                                                             version)
        else:
            vra_content = self.get_blueprint_content(blueprint_id)
        return yaml.load(vra_content, Loader=yaml.FullLoader)

    def list_iaas_machines(self, query_params=None):
        """
        Returns a list of IaaS machines. The query_params dict can be used to
        filter the results.
        :param query_params:
        :return:
        """
        if query_params is None:
            query_params = {}
        url = f'/iaas/api/machines'
        url = self.add_query_params_to_url(url, query_params)
        response_json = self.submit_request(url)
        return response_json

    def get_iaas_machine(self, machine_id):
        """
        Returns the IaaS machine object for the provided machine_id
        :param machine_id:
        :return:
        """
        url = f'/iaas/api/machines/{machine_id}'
        response_json = self.submit_request(url)
        return response_json

    def get_iaas_machine_from_instance_uuid(self, instance_uuid):
        """
        Returns the IaaS machine object for the provided instance_uuid
        :param instance_uuid:
        :return:
        """
        response_json = self.list_iaas_machines(
            {"$filter": f'customProperties.instanceUUID eq {instance_uuid}'}
        )
        if response_json["numberOfElements"] != 1:
            raise Exception(f'{response_json["numberOfElements"]} results found'
                            f' for instance_uuid: {instance_uuid}')
        return response_json["content"][0]

    def add_query_params_to_url(self, url, query_params: dict):
        """
        Adds the provided query_params to the provided url
        :param url:
        :param query_params:
        :return:
        """
        if query_params:
            url += f'?{urlencode(query_params)}'
        return url

    def get_headers(self):
        """
        Returns the headers to be used for API calls
        :return:
        """
        headers = {'Content-Type': 'application/json',
                   'Accept': 'application/json'}
        token = self.get_token(headers)
        headers['Authorization'] = f'Bearer {token}'
        return headers

    def get_base_url(self):
        """
        Returns the base url to be used for API calls
        :return:
        """
        conn = self.conn_info
        base_url = f'{conn.protocol}://{conn.ip}'
        if conn.port:
            base_url += f':{conn.port}'
        return base_url

    def get_connection_info(self):
        """
        Returns the ConnectionInfo object for the provided conn_info_id
        :return:
        """
        conn = ConnectionInfo.objects.get(id=self.conn_info_id)
        assert isinstance(conn, ConnectionInfo)
        return conn

    def get_user_by_username(self, username, org_id):
        """
        Returns the user object for the provided username and org_id
        :param username:
        :param org_id:
        :return:
        """
        url = (f"/csp/gateway/am/api/orgs/{org_id}/users/search"
               f"?userSearchTerm={username}")
        response_json = self.submit_request(url)
        if len(response_json["results"]) != 1:
            raise Exception(f'{len(response_json["results"])} results found'
                            f' for username: {username}')
        return response_json["results"][0]["user"]

    def get_token(self, headers):
        """
        Returns the token to be used for API calls
        :param headers:
        :return:
        """
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
        """
        Submits a request to the provided url_path using the provided method
        and returns the response as a dict
        :param url_path:
        :param method:
        :param kwargs:
        :return:
        """
        headers = self.headers
        base_url = self.base_url
        url = f'{base_url}{url_path}'
        if method == "get":
            response = requests.get(url, headers=headers,
                                    verify=VERIFY_CERTS)
        elif method == 'post':
            response = requests.post(url, headers=headers,
                                     verify=VERIFY_CERTS, **kwargs)
        else:
            raise Exception(f"Method: {method} not supported")
        try:
            response.raise_for_status()
        except Exception as e:
            logger.error(f'Error encountered for URL: {url}, details: '
                         f'{e.response.content}')
            raise
        return response.json()


class AriaOrchestratorConnection(AriaAutomationConnection):
    def __init__(self, conn_info_id):
        super().__init__(conn_info_id)
        self.base_url = f'{self.base_url}/vco/api'

    def list_workflows(self):
        """
        Returns a list of all workflows from vRO
        :return:
        """
        url = '/workflows'
        return self.submit_request(url)["link"]

    def generate_options_for_workflow_id(self):
        """
        Returns a list of tuples containing the workflow href and name to be
        used in a generated_options_for field
        :return:
        """
        workflows = self.list_workflows()
        return [(w["href"], self.get_workflow_name(w)) for w in workflows]

    def get_workflow_name(self, workflow):
        """
        Returns the name of a workflow from the workflow object
        :param workflow:
        :return:
        """
        for attribute in workflow["attributes"]:
            if attribute["name"] == "name":
                return attribute["value"]

    def get_workflow(self, workflow_id):
        """
        Returns the workflow object for the provided workflow_id
        :param workflow_id:
        :return:
        """
        url = f'/workflows/{workflow_id}'
        return self.submit_request(url)

    def get_workflow_inputs(self, workflow_id):
        """
        Returns the input parameters for the provided workflow_id
        :param workflow_id:
        :return:
        """
        workflow = self.get_workflow(workflow_id)
        return workflow["input-parameters"]

    def execute_workflow(self, workflow_id, parameters: dict):
        """
        Executes a workflow with the provided parameters. The parameters
        should be provided as a dict
        """
        formatted_params = self.format_params(workflow_id, parameters)
        logger.debug(f"Formatted params: {formatted_params}")
        data = {"parameters": formatted_params}
        url = f'/workflows/{workflow_id}/executions'
        return self.submit_request(url, method="post", json=data)

    def format_params(self, workflow_id, parameters):
        """
        Converts params to a list of dicts where each dict contains the
        following keys based off of the input type defined in the vRA workflow:
            name: The name of the parameter
            type: The type of the parameter (string, number, boolean, etc)
            value: value to pass ex: {"string": {"value": "My Value"}}
            scope: The scope of the parameter (local, shared, etc)
        """
        output_params = []
        workflow = self.get_workflow(workflow_id)
        input_params = workflow["input-parameters"]
        supported_types = ["string", "number", "boolean", "Array/string",
                           "Array/number", "Array/boolean", "Array/Properties",
                           "Properties"]

        for key, value in parameters.items():
            key_found = False
            if type(value) is dict:
                if self.check_valid_value_dict(value):
                    output_params.append(value)
                    continue
            for param in input_params:
                if param["name"] == key:
                    param_type = param["type"]
                    if param_type not in supported_types:
                        raise Exception(f"Type: {param['type']} inputs are not"
                                        f" supported for vRO Workflow")
                    value = self.construct_param_value(param_type, value)
                    logger.debug(f"param_type: {param_type}, value: {value}")
                    param_dict = {"name": key, "type": param["type"],
                                  "value": value, "scope": "local"}
                    output_params.append(param_dict)
                    key_found = True
            if not key_found:
                logger.warning(f"Key: {key} not found in workflow inputs")

        return output_params

    def construct_param_value(self, param_type, input_value):
        """
        Constructs the value for a workflow input based on the type of the
        workflow input. The value should be a string, number, boolean, or
        Properties dict.
        :param param_type:
        :param input_value:
        :return:
        """
        if param_type in ["string", "number", "boolean", "Properties"]:
            value = self.get_type_value(param_type, input_value)
        elif param_type.startswith("Array/"):
            value_dict = {"array": {"elements": []}}
            param_type = param_type.split("/")[1]
            if type(input_value) is list:
                for element in input_value:
                    value_dict["array"]["elements"].append(
                        self.get_type_value(param_type, element)
                    )
            else:
                value_dict["array"]["elements"].append(
                    self.get_type_value(param_type, input_value)
                )
            value = value_dict
        else:
            raise Exception(f"Type: {param_type} inputs are not supported for "
                            f"vRO Workflow")
        return value

    @staticmethod
    def get_type_value(param_type, value):
        """
        Returns the value for a workflow input based on the type of the
        workflow input. The value should be a string, number, boolean, or
        Properties dict.
        :param param_type: The type of the workflow input
        :param value: The value to pass to the workflow input
        """

        def create_properties_value(input_value):
            """
            Creates a properties value for a workflow input. The input_value
            should be a dict of the form:
            {
                "key": "value",
                "key2": "value2"
            }
            """
            value_dict = {"properties": {"property": []}}
            for key, value in input_value.items():
                if type(value) == dict:
                    value = json.dumps(value)
                value = {"string": {"value": value}}
                value_dict["properties"]["property"].append(
                    {"key": key, "value": value}
                )
            return value_dict

        if param_type == "string":
            value = {param_type: {"value": value}}
        elif param_type == "number":
            value = {param_type: {"value": int(value)}}
        elif param_type == "boolean":
            value = {param_type: {"value": bool(value)}}
        elif param_type == "Properties":
            value = create_properties_value(value)
        else:
            raise Exception(f"Type: {param_type} inputs are not"
                            f" supported for vRO Workflow")
        return value

    def wait_for_workflow_execution(self, workflow_id, execution_id,
                                    sleep_time=5):
        """
        Waits for the workflow execution to complete. Returns the state of
        the workflow execution when it is done.
        """
        done_states = ["completed", "failed", "canceled"]
        state = "running"
        max_sleep = 300
        total_sleep = 0
        while state not in done_states:
            state = self.get_workflow_execution_state(workflow_id,
                                                      execution_id)
            set_progress(f'Workflow execution state: {state}, sleeping for '
                         f'{sleep_time} seconds')
            time.sleep(sleep_time)
            total_sleep += sleep_time
            if total_sleep > max_sleep:
                raise Exception(f"Workflow execution did not complete after "
                                f"{max_sleep} seconds. State: {state}")
        return state

    def check_valid_value_dict(self, value):
        """
        Checks to see if the value is already formatted properly for vRO
        :param value:
        :return:
        """
        try:
            value_name = value["name"]
            value_type = value["type"]
            value_value = value["value"]
            value_scope = value["scope"]
            if value_name and value_type and value_value and value_scope:
                return True
        except KeyError:
            pass
        return False

    def list_workflow_executions(self, workflow_id):
        """
        Returns a list of workflow executions for the provided workflow_id
        :param workflow_id:
        :return:
        """
        url = f'/workflows/{workflow_id}/executions'
        return self.submit_request(url)

    def get_workflow_execution(self, workflow_id, execution_id):
        """
        Returns the workflow execution object for the provided workflow_id and
        execution_id
        :param workflow_id:
        :param execution_id:
        :return:
        """
        url = f'/workflows/{workflow_id}/executions/{execution_id}'
        return self.submit_request(url)

    def get_workflow_execution_state(self, workflow_id, execution_id):
        """
        Returns the state of the workflow execution for the provided
        workflow_id and execution_id
        :param workflow_id:
        :param execution_id:
        :return:
        """
        url = f'/workflows/{workflow_id}/executions/{execution_id}/state'
        return self.submit_request(url)["value"]

    def list_objects_for_type(self, object_type, display_attribute: list,
                              query=None):
        """
        Returns a list of objects for the provided object type. The
        return_attributes list should be a list of attributes to return
        for each object. If no attributes are provided, all attributes
        will be returned.
        :param object_type: The type of object to return. ex: VC:VirtualMachine
        :param display_attribute: The attribute to use as the display name
        :param query: A query to filter the results. ex: name eq 'myVM'
        """
        namespace, type = object_type.split(":")
        url = f'/catalog/{namespace}/{type}'
        return self.submit_request(url)
