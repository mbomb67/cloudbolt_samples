from django.shortcuts import render

from extensions.views import admin_extension
from xui.gitlab.api_wrapper import GitLabConnector


@admin_extension(title="GitLab Integration",
                 description="GitLab integration library for CloudBolt "
                             "scripts and plugins.")
def gitlab_admin(request, **kwargs):
    context= {
        'docstring': GitLabConnector.__doc__,
        'a':'b'
    }
    return render(request, 'gitlab/templates/gitlab_admin.html',
                  context=context)