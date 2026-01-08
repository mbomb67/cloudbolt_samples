# wrapper.nova.client.get_endpoint()

def get_networks(wrapper):
    token = wrapper.keystone.session.get_token()
    base_url = get_service_endpoint(wrapper, "neutron")
    base_url = f'{base_url}v2.0'
    headers = {
        'Content-Type': 'application/json',
        'X-Auth-Token': token
    }
    response = wrapper.keystone.session.get(f'{base_url}/networks', verify=False)
    """
    Alternative:
    import requests
    response = requests.get(f'{base_url}/networks', headers=headers, verify=False)
    """
    return response.json()

def get_images(wrapper):
    base_url = get_service_endpoint(wrapper, "glance")
    base_url = f'{base_url}v2'
    response = wrapper.keystone.session.get(f'{base_url}/images', verify=False)
    """
    Alternative:
    import requests
    token = wrapper.keystone.session.get_token()
    headers = {
        'Content-Type': 'application/json',
        'X-Auth-Token': token
    }
    response = requests.get(f'{base_url}/images', headers=headers, verify=False)
    """

    # Regardless of which method is used, will need to use the "next" link to
    # get all images
    return response.json()


def get_servers(wrapper, project_id):
    endpoint_template = get_service_endpoint(wrapper, "nova")
    base_url = endpoint_template % {"tenant_id": project_id}
    url = f'{base_url}/servers'
    response = wrapper.keystone.session.get(url, verify=False)
    """
    Alternative:
    import requests
    token = wrapper.keystone.session.get_token()
    headers = {
        'Content-Type': 'application/json',
        'X-Auth-Token': token
    }
    response = requests.get(f'{base_url}/servers', headers=headers, verify=False)
    """
    return response.json()


def get_service_endpoint(wrapper, service_name, interface="public"):
    services = wrapper.keystone.services.list(service_name)
    if not services:
        raise Exception(f'No service found with name {service_name}')
    service = services[0]
    endpoints = wrapper.keystone.endpoints.list(service=service,
                                                interface=interface)
    if endpoints:
        return endpoints[0].url
    else:
        raise Exception(f'No endpoint found for service {service_name} with '
                        f'interface {interface}')