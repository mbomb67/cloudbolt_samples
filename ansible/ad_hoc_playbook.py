import json

from common.methods import generate_string_from_template, set_progress
from connectors.ansible.models import AnsibleConf

ANSIBLE_ID = "{{ansible_manager}}"
PLAYBOOK_PATH = "{{playbook_path}}"
LIMIT = "{{limit}}"


def generate_options_for_ansible_manager(**kwargs):
    hosts = AnsibleConf.objects.all()
    options = [(host.id, host.name) for host in hosts]
    if not options:
        options = [('', '--- First create a Configuration Manager ---')]
    return options


def run(job, **kwargs):
    """
    A CloudBolt Plugin that will execute an Ad Hoc Ansible Playbook
    This will read all Parameters set on the resource and pass them in as Extra
    Vars to the Playbook
    Hosts and Limit options are optional
    """
    resource = kwargs.get('resource')
    ansible = AnsibleConf.objects.get(id=ANSIBLE_ID)
    extra_vars = json.dumps(resource.get_cf_values_as_dict())
    script_contents = generate_playbook_command(PLAYBOOK_PATH, LIMIT, ansible,
                                                extra_vars, job, resource)
    # set_progress(f'script_contents: {script_contents}')
    output = ansible.connection_info.execute_script(
        script_contents=script_contents, timeout=600
    )
    return "SUCCESS", output, ""


def generate_playbook_command(playbook_path, limit, ansible, extra_vars,
                              job, resource):
    inventory_path = ansible.inventory_path
    script_contents = f"ansible-playbook {playbook_path} -i {inventory_path}"
    if limit:
        script_contents += f' --limit "{limit}"'

    if extra_vars:
        # Extra Vars could use Django Templating to reference things we know
        group = job.get_resource().group
        local_context = {"resource": resource}
        extra_vars = generate_string_from_template(
            template=extra_vars,
            group=group,
            env=None,
            os_build=None,
            context=local_context
        )
        script_contents += f" --extra-vars='{extra_vars}'"
    return script_contents
