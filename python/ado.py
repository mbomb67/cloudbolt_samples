import urllib.parse

def generate_raw_url(file_url):
    """
    Generate the raw URL needed in CloudBolt to use the "Fetch from URL" option
    in a CloudBolt action. This method assumes that you are able to use TfsGit
    As your source provider, and that your Project and Repo Names are the same
    :param file_url: The URL of the file in Azure DevOps
    Ex: https://dev.azure.com/cloudbolt-sales-demo/_git/CMP?path=/python_samples/xaas_build.py
    """
    if file_url.find('/_git/') == -1:
        raise Exception('The URL entered appears to not be a GIT file')
    if file_url.find('?path=') == -1:
        raise Exception('The URL entered does not include a path, the URL '
                        'should point to a file')
    git_split = file_url.split('/_git/')
    project, args = git_split[1].split('?')
    args_split = args.split('&')
    branch = ''
    path = ''
    for arg in args_split:
        key, value = arg.split('=')
        if key == 'path':
            if value.find('%') == -1:
                path = urllib.parse.quote(value, safe='')
            else:
                path = urllib.parse.quote(value)
        if key == 'version' and value.find('GB') == 0:
            branch = value[2:]
    if not path:
        raise Exception('Path was not found in the URL entered')
    if not branch:
        branch = 'main'
    org = git_split[0].split('/')[-1]
    url_prefix = f'{git_split[0].split(":")[0]}://{git_split[0].split("/")[-2]}'
    raw_url = f'{url_prefix}/{org}/{project}/_apis/sourceProviders/TfsGit/' \
              f'filecontents?repository={project}&path={path}&commitOrBranch=' \
              f'{branch}&api-version=5.0-preview.1'
    return raw_url
