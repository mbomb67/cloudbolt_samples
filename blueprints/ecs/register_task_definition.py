from infrastructure.models import Environment


def connect_to_ecs(env):
    """
    Return boto connection to the ecs in the specified environment's region.
    """
    rh = env.resource_handler.cast()
    wrapper = rh.get_api_wrapper()
    client = wrapper.get_boto3_client(
        'ecs',
        rh.serviceaccount,
        rh.servicepasswd,
        env.aws_region
    )
    return client


def generate_options_for_network_mode(field, control_value=None, **kwargs):
    if not control_value:
        options = [('', '--- First, Select Compatibilities---')]
        return options
    try:
        control_value.index('FARGATE')
        return [("awsvpc", "awsvpc")]
    except ValueError:
        return [("none", "none"), ("bridge", "bridge"), ("awsvpc", "awsvpc"),
                ("host", "host")]


def generate_options_for_protocol(**kwargs):
    return ["TCP", "UDP"]


def generate_options_for_essential(**kwargs):
    return [(True, "YES"), (False, "NO")]


def generate_options_for_attach_to_service(resource, **kwargs):
    env = Environment.objects.get(id=resource.env_id)
    client = connect_to_ecs(env)
    response = client.list_services(
        cluster=resource.cluster_name,
    )
    services = ['None']
    for res in response['serviceArns']:
        services.append(res.split('/')[-1])

    return services


def generate_options_for_create_service(**kwargs):
    return [(True, "YES"), (False, "NO")]


def generate_options_for_requires_compatibilities(**kwargs):
    return ['EC2', 'FARGATE', 'EXTERNAL']


def run(resource, *args, **kwargs):
    env = Environment.objects.get(id=resource.env_id)
    family = "{{ family_name }}"
    network_mode = "{{ network_mode }}"
    container_name = "{{ container_name }}"
    container_port = int("{{ container_port }}")
    host_port = int("{{ host_port }}")
    essential = bool("{{ essential }}")
    memory = int("{{ memory }}")
    create_service = bool("{{ create_service }}")
    requires_compatibilities = "{{requires_compatibilities}}"

    container_definitions = [
        {
            'name': container_name,
            'image': "{{ docker_image }}",
            'portMappings': [
                {
                    "containerPort": container_port,
                    "hostPort": host_port,
                    "protocol": "{{ protocol }}"
                }
            ],
            'essential': essential,
            'memory': memory
        }
    ]

    client = connect_to_ecs(env)

    try:
        client.register_task_definition(
            family=family,
            networkMode=network_mode,
            containerDefinitions=container_definitions,
            requiresCompatibilities=[requires_compatibilities]
        )
        if create_service:
            client.create_service(
                cluster=resource.cluster_name,
                serviceName=resource.cluster_name + "Service",
                taskDefinition=family,
                desiredCount=1,
            )
    except Exception as error:
        return "FAILURE", "", f"{error}"

    return "SUCCESS", "", ""
