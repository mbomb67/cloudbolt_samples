"""
UI Extension for OpenScap on Enterprise Linux

Confirmed Support:
CentOS7, RHEL7

Requires:
yum install openscap-scanner scap-security-guide
"""
from infrastructure.models import Server, CustomField
from utilities.logger import ThreadLogger
from utilities.decorators import dialog_view
from utilities.exceptions import NotFoundException
from utilities.ssh_options_service import SSHOptions
from common.methods import shlex_quote, get_file_for_key_material, execute_command
from django.shortcuts import get_object_or_404, render, redirect
from extensions.views import tab_extension, TabExtensionDelegate
from tempfile import NamedTemporaryFile
from django.conf import settings
from xui.openscap import forms
from datetime import datetime
import glob
import os

LOGGER = ThreadLogger(__name__)
report_target_path = os.path.join(
    settings.MEDIA_ROOT,
    'openscap_reports'
)


def copy_file_from_target(
    ip,
    timeout=120,
    username=None,
    password=None,
    key_name=None,
    key_location=None,
    guest_file_path=None,
    file_path=None,
    ssh_options: SSHOptions = None,
) -> str:
    """
    Given an IP and data to copy, copy the data from the target and save
    it to a new file.
    Returns the local file_path
    """
    # determine authentication method
    keyfile_args, sshpass_args = "", ""
    keyfile = None
    is_temp_file = False
    if key_name:
        keyfile, is_temp_file = get_file_for_key_material(
            key_name, key_location=key_location
        )
        if not keyfile:
            raise NotFoundException(
                "Could not find required key material " "{}".format(key_name)
            )
        keyfile_args = "-i '{}'".format(keyfile)
    else:
        sshpass_args = "sshpass -p {}".format(shlex_quote(password))
    try:
        if not guest_file_path:
            raise NotFoundException("Need guest_file_path")
        if not file_path:
            # save data to a tempfile
            script = NamedTemporaryFile()
            file_path = script.name
        # Default scp options
        scp_options = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
        # Override default scp options if SSHOptions is utilized
        if ssh_options:
            scp_options = ssh_options.options
            LOGGER.info(f"Using SSHOptions Service. Options passed: '{scp_options}'")
        script = (
            "{sshpass_args} "
            "scp {keyfile_args} "
            # do not check if server is in known_hosts
            "{scp_options} "
            "{username}@{ip}:{guest_file_path} "
            "{file_path}".format(**locals())
        )
        execute_command(
            script,
            timeout=timeout,
            strip=[password] if password else [],
            stream_remote_output=False,
        )
    except Exception as e:
        # Just doing try-except so can have finally for temp file deletion,
        # so re-raise
        raise e
    finally:
        # delete temporary key file if necessary
        if keyfile and is_temp_file:
            os.remove(keyfile)
    return file_path


def create_custom_fields():
    CustomField.objects.update_or_create(
        name='openscap_schedule',
        defaults={'type': 'STR', 'label': 'OpenScap Schedule'}
    )


def get_version_info(server):
    script = '''
    oscap --version |grep -i "version:"
    '''
    stdout = server.execute_script(
        script_contents=script, timeout=120, show_streaming_output=True, remove_after_run=True,
        runas_username=server.username, runas_password=server.password, run_with_sudo=False)
    stdout_list = stdout.split('\n')
    version_info = []
    for i in stdout_list:
        if 'Version' in i:
            line = i.split('Version:')
            version_info.append({"label": line[0], "version": line[1]})
    return version_info


def get_policies(server):
    script = '''
    #!/bin/bash

    tmpfile="/tmp/profiles"
    echo -n > /tmp/profiles

    for i in $(ls -1 /usr/share/xml/scap/ssg/content/ssg-*-ds.xml); do
      file=$(basename $i)
      oscap info --profiles $i | sed 's,^,'"$file:"',' >> $tmpfile
      # sed -i 's,^,'"$file:"',' $tmpfile
    done
    cat $tmpfile
    '''
    stdout = server.execute_script(
        script_contents=script, timeout=120, show_streaming_output=True, remove_after_run=True,
        runas_username=server.username, runas_password=server.password, run_with_sudo=False)
    items = stdout.split('\n')
    policies = []
    for i in items:
        if i != "":
            policies.append(i)
    return policies


def create_report_path():
    if not os.path.isdir(report_target_path):
        os.mkdir(report_target_path)


def get_reports(server):
    create_report_path()
    reports = []
    for filename in glob.glob(f'{report_target_path}/report_{server.id}_*'):
        reports.append(os.path.basename(filename))
    return reports


def run_evaluation_for_server(server, policy, profile):
    timestamp = datetime.now().isoformat()
    report = f'report_{server.id}_{timestamp}.html'
    script = f'''
    #!/bin/env bash
    oscap xccdf eval --profile {profile} --report /tmp/{report} /usr/share/xml/scap/ssg/content/{policy}
    '''
    server.execute_script(script_contents=script, timeout=120, show_streaming_output=True, remove_after_run=True,
                          runas_username=server.username, runas_password=server.password, run_with_sudo=False)
    create_report_path()
    username, password, key_name, key_location = server.credentials_for_script(None, None, None)
    copy_file_from_target(
        server.ip,
        username=username,
        password=password,
        key_name=key_name,
        key_location=key_location,
        guest_file_path=f'/tmp/{report}',
        file_path=f'{report_target_path}/{report}'
    )


@dialog_view
def run_evaluation(request, server_id):
    server = get_object_or_404(Server, pk=server_id)
    if request.method == 'POST':
        form = forms.RunEvaluationForm(request.POST)
        if form.is_valid():
            policy = form.cleaned_data['policy']
            p_file = policy.split(':')[0]
            p_profile = policy.split(':')[1]
            run_evaluation_for_server(server, policy=p_file, profile=p_profile)
            return redirect('/servers/{}/'.format(server_id))
    elif request.method == 'GET':
        form = forms.RunEvaluationForm(
            request=request,
            policies=get_policies(server),
        )
    else:
        form = forms.RunEvaluationForm()
    return dict(
        form=form,
        title='Run Evalution',
        submit='Run',
        action_url=f'/servers/{server_id}/run_evaluation/',
    )


class TabDelegate(TabExtensionDelegate):
    def should_display(self):
        # return ChefNode.objects.filter(cb_server_id=self.instance.id).exists()
        return 'OpenScap' in self.instance.labels


@tab_extension(model=Server,
               delegate=TabDelegate,
               title='OpenScap',
               description='OpenScap Server Tab')
def server_tab_chef(request, obj_id=None, logger=None):
    """
    OpenScap Server Tab Extension
    """
    if not logger:
        logger = LOGGER

    # create_custom_fields()
    server = get_object_or_404(Server, pk=obj_id)
    version_info = get_version_info(server)

    return render(request, 'openscap/templates/server_tab.html', dict(
        server=server,
        version_info=version_info,
        reports=get_reports(server)
    ))
