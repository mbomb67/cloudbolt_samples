"""
Reach out to GitLab and get multiple files. Save to the resource
"""

if __name__ == '__main__':
    import django
    django.setup()
from common.methods import set_progress
import sys
from xui.gitlab.api_wrapper import GitLabConnector


def run(job, *args, **kwargs):
    resource = job.resource_set.first()
    if resource:
        # GitLab Params
        gitlab_name = '{{ gitlab_name }}'
        project_id = '{{ project_id }}'
        params_path = '{{ params_path }}'
        template_path = '{{ template_path }}'
        git_branch = '{{ git_branch }}'

        with GitLabConnector(gitlab_name) as gitlab:
            template_string = gitlab.get_raw_file_as_json(project_id,
                                                          template_path, git_branch)
        set_progress(f"GitLab Get Files running for resource: {resource}")
        try:
            pass
        except:
            error_string = (f'Error: {sys.exc_info()[0]}. {sys.exc_info()[1]}, '
                            f'line: {sys.exc_info()[2].tb_lineno}')
            set_progress(f'Error: {error_string}')
            raise Exception(f'GitLab Get Files Failed. {error_string}')

    else:
        set_progress("Resource was not found")
        return "SUCCESS", "Resource was not found", ""


if __name__ == '__main__':
    run()