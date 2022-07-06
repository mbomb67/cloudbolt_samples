"""
A CloudBolt plugin that allows you to copy a file to a Server under CloudBolt
Management.
"""
from common.methods import set_progress
from infrastructure.models import Server
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


"""
The following code is not used directly in this plugin, but is used in a 
Generated Options action for the server_id field - this allows you to search 
for a server by name to select when provisioning 

from infrastructure.models import Server
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def suggest_options(custom_field, query, **kwargs):
    servers = Server.objects.filter(hostname__istartswith=query)
    logger.info(f'copy_file: servers: {servers}')
    return [(s.id, s.hostname) for s in servers]
    
def get_options_list(*args, **kwargs):
    return None
"""

def generate_options_for_non_prod_services(**kwargs):
    options = generate_options_for_production_services()
    options.append((3389, "rdp (3389)"))
    options.append((22, "ssh (22)"))
    return options


def generate_options_for_production_services(**kwargs):
    options = [
        (443, "https (443)"),
        (80, "http (80)"),
        (53, "dns (53)"),
        (123, "ntp (123)"),
    ]
    return options


def run(job, **kwargs):
    # Input Variables
    server_id = int("{{server_id}}")
    # File path can use Django templating.
    # {# ex. /home/ansible/configs/{{job.parent_job.get_order.id}}.yml #}
    file_name = "/home/ansible/configs/{{resource.tn_interface_name}}.yml"

    resource = kwargs.get("resource")
    server = Server.objects.get(id=server_id)
    set_progress(f'Saving config file to: {file_name} on server: '
                 f'{server.hostname}')
    file_contents = get_yaml_file()
    script_contents = create_file_creation_script(file_contents, file_name)
    resource.tn_yaml_file = file_contents
    resource.save()
    server.execute_script(script_contents=script_contents)


def get_yaml_file():
    nps = {{non_prod_services}}
    ps = {{production_services}}
    np_string = "".join([f'    - {np}\n'for np in nps])[:-1]
    p_string = "".join([f'    - {p}\n'for p in ps])[:-1]

    return f"""
# Create Firewall Rule Config

- parameters:
  non_prod_sources: {{non_prod_sources}}
  non_prod_destinations: {{non_prod_destinations}} 
  non_prod_services: 
{np_string}
  production_sources: {{production_sources}}
  production_destinations: {{production_destinations}}
  production_services: 
{p_string}
  peak_bandwidth: {{peak_bandwidth}}
  bandwidth_increase: {{bandwidth_increase}}    
"""


def create_file_creation_script(file_contents, file_name):
    return f"""
#!/bin/bash

filename="{file_name}"
filepath="{'/'.join(file_name.split('/')[:-1])}"
mkdir -p $filepath

cat << EOF > $filename
{file_contents}
EOF

cat $filename
"""
