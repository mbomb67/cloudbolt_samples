import requests

if __name__ == '__main__':
    import django

    django.setup()

from utilities.models import ConnectionInfo
from utilities.rest import RestConnection
import sys
from common.methods import set_progress
import urllib.parse
import json

class GitLabConnector(RestConnection):
    """
    This is a context manager class available to CloudBolt Plugins that
    facilitates easy API connectivity from a CloudBolt host to a GitLab host
    given the name of a ConnectionInfo object as a string.

    Installation Instructions: 
    1. Create a Connection Info for gitlab. This must be labelled as 'gitlab'
    2. In the Connection Info, you can either:
        a. Store the private token as the password value
        b. OR use the headers field and enter the following (replacing with your token:
            {
                'PRIVATE-TOKEN': '<Your token goes here>',
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }

    Standard REST Call Example:
        from from xui.gitlab.api_wrapper import GitLabConnector
        with GitLabConnector("name-of-connection-info-object") as gitlab:
            response = gitlab.get("/projects/")

    Get Raw Fle Example: 
        from xui.gitlab.api_wrapper import GitLabConnector
        with GitLabConnector("gitlab") as gitlab:
            raw_file = gitlab.get_raw_file('25216144','cloudformation_sample.template','master')

    Authentication, headers, and url creation is handled within this class,
    freeing the caller from having to deal with these tasks.

    A boolean parameter called verify_certs with default value of False, is
    provided in the constructor in case the caller wants to enable SSL cert
    validation.
    """

    def __init__(self, name: str = "gitlab", verify_certs: bool = False):
        if not verify_certs:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        try:
            conn_info = ConnectionInfo.objects.get(
                name__iexact=name,
                labels__name='gitlab'
            )
        except:
            err_str = (f'ConnectionInfo could not be found with name: {name},'
                       f' and label gitlab')
            raise Exception(err_str)
        self.conn_info = conn_info
        super().__init__(conn_info.username, conn_info.password)
        if conn_info.headers:
            headers_dict = json.loads(conn_info.headers)
            try: 
                headers_dict["PRIVATE-TOKEN"]
                self.headers = headers_dict
            except KeyError:
                if conn_info.password:
                    headers_dict["PRIVATE-TOKEN"] = conn_info.password
                    self.headers = headers_dict
                else: 
                    err_string = (f'Connection Info did not contain either '
                        f'PRIVATE-TOKEN in the headers or a password. Check '
                        f'the Connection Info {name} and try again.')
                    raise Exception(err_string)
        else:
            self.headers = {
                'PRIVATE-TOKEN': conn_info.password,
                'Accept': 'application/json',
                'Content-Type': 'application/json'
            }
        self.verify_certs = verify_certs
        self.base_url = conn_info.protocol + '://'
        self.base_url += conn_info.ip
        self.base_url += f':{conn_info.port}'
        self.base_url += '/api/v4'

    def __enter__(self):
        return self

    def __getattr__(self, item):
        if item == 'get':
            return lambda path, **kwargs: requests.get(
                self.base_url + path,
                auth=None,
                headers=self.headers,
                verify=self.verify_certs,
                **kwargs
            )
        elif item == 'post':
            return lambda path, **kwargs: requests.post(
                self.base_url + path,
                auth=None,
                headers=self.headers,
                verify=self.verify_certs,
                **kwargs
            )
        elif item == 'delete':
            return lambda path, **kwargs: requests.delete(
                self.base_url + path,
                auth=None,
                headers=self.headers,
                verify=self.verify_certs,
                **kwargs
            )
        elif item == 'put':
            return lambda path, **kwargs: requests.put(
                self.base_url + path,
                auth=None,
                headers=self.headers,
                verify=self.verify_certs,
                **kwargs
            )
        else:
            return item

    def __repr__(self):
        return 'GitLabManager'

    def get_raw_file_as_json(self, project_id, file_path, git_branch):
        try:
            # Encode the file_path URI
            file_path = urllib.parse.quote(file_path, safe='')
            path = (f'/projects/{project_id}/repository/files/{file_path}/raw'
                    f'?ref={git_branch}')
            set_progress(f'Submitting request to GitLab URL: {path}')
            r = self.get(path)
            r.raise_for_status()
            r_json = r.json()
            raw_file_json = json.dumps(r_json)
            return raw_file_json
        except:
            error_string = (f'Error: {sys.exc_info()[0]}. {sys.exc_info()[1]}, '
                            f'line: {sys.exc_info()[2].tb_lineno}')
            set_progress(error_string)
            raise Exception(f'GitLab REST call failed. {error_string}')


if __name__ == '__main__':
    with GitLabConnector('gitlab') as gitlab:
        response = gitlab.get('/projects/')
    import json

    print(json.dumps(response.json(), indent=True))
