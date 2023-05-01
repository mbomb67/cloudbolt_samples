"""
Build service item action for AWS Lambda function.
Updated - 5/1/2023
"""
from common.methods import set_progress
from infrastructure.models import CustomField
from infrastructure.models import Environment
import boto3
from urllib.request import urlopen
from django.conf import settings
import os
import json
from accounts.models import Group
from botocore.exceptions import ClientError

def sort_dropdown_options(data, placeholder=None, is_reverse=False):
    """
    Sort dropdown options 
    """
    # remove duplicate option from list
    data = list(set(data))

    # sort options
    sorted_options = sorted(data, key=lambda tup: tup[1].lower(), reverse=is_reverse)

    if placeholder is not None:
        sorted_options.insert(0, placeholder)

    return {'options': sorted_options, 'override': True}
    
def generate_options_for_env_id(**kwargs):
    """
    Generate AWS region options
    """
    group_name = kwargs["group"]

    try:
        group = Group.objects.get(name=group_name)
    except Exception as err:
        return []


    # fetch all group environment
    envs = group.get_available_environments()

    aws_envs= [env for env in envs if env.resource_handler.resource_technology.name == "Amazon Web Services"]
    options = []

    for env in aws_envs:

        options.append((env.id, env.name))

    return sort_dropdown_options(options, ("", "-----Select Environment-----"))

def generate_options_for_runtime(server=None, **kwargs):
    return ['nodejs', 'nodejs4.3', 'nodejs6.10', 'nodejs8.10',
            'java8', 'python2.7', 'python3.6', 'python3.7', 'python3.8', 'python3.9', 'dotnetcore1.0',
            'dotnetcore2.0', 'dotnetcore2.1', 'nodejs4.3-edge', 'go1.x'
    ]

def create_role(env):
    rh = env.resource_handler.cast()
    client = boto3.client(
        'iam',
        region_name=env.aws_region,
        aws_access_key_id=rh.serviceaccount,
        aws_secret_access_key=rh.servicepasswd,
    )
    role_policy = {
      "Version": "2012-10-17",
      "Statement": [
        {
          "Sid": "",
          "Effect": "Allow",
          "Principal": {
            "Service": "lambda.amazonaws.com"
          },
          "Action": "sts:AssumeRole"
        }
      ]
    }

    try:
        response = client.create_role(
          RoleName='LambdaBasicExecution',
          AssumeRolePolicyDocument=json.dumps(role_policy),
        )
    except ClientError as e:
        if e.response['Error']['Code'] == 'EntityAlreadyExists':
            set_progress("Role is already exists")
            return client.get_role(RoleName='LambdaBasicExecution')
        else:
            set_progress("Unexpected error: %s" % e)
            return "FAILURE", "", str(e)

    return response


def run(job, logger=None, **kwargs):
    env = Environment.objects.get(id='{{ env_id }}')
    aws_region = env.aws_region
    rh = env.resource_handler.cast()
    function_name = '{{ function_name }}'
    runtime = '{{ runtime }}'
    handler = '{{ handler }}'
    zip_file = '{{ zip_file }}'

    CustomField.objects.get_or_create(
        name='aws_rh_id', type='STR',
        defaults={'label':'AWS RH ID', 'description':'Used by the AWS blueprints'}
    )
    CustomField.objects.get_or_create(
        name='aws_region', type='STR',
        defaults={'label':'AWS Region', 'description':'Used by the AWS blueprints', 'show_as_attribute':True}
    )
    CustomField.objects.get_or_create(
        name='aws_function_name', type='STR',
        defaults={'label':'AWS Lambda function name', 'description':'Used by the AWS Lambda blueprint', 'show_as_attribute':True}
    )

    set_progress('Downloading %s...' % zip_file)
    
    if zip_file.startswith(settings.MEDIA_URL):
        set_progress("Converting relative URL to filesystem path")
        file = file.replace(settings.MEDIA_URL, settings.MEDIA_ROOT)
        
    file = os.path.join(settings.MEDIA_ROOT, zip_file)
    if not file.endswith('.zip'):
        return "FAILURE", "FAILURE", "Please upload the zip file"    
    zip_data = open(file, 'rb').read()
    
    set_progress('Connecting to AWS...')
    client = boto3.client(
        'lambda',
        region_name=aws_region,
        aws_access_key_id=rh.serviceaccount,
        aws_secret_access_key=rh.servicepasswd,
    )

    set_progress('Creating lambda %s...' % function_name)
    role = create_role(env)
    response = client.create_function(
        FunctionName=function_name,
        Runtime=runtime,
        Role=role['Role']['Arn'],
        Handler=handler,
        Code={'ZipFile': zip_data},
    )

    set_progress('Function ARN: %s' % response['FunctionArn'])
    assert response['FunctionName'] == function_name

    resource = kwargs.get('resource')
    resource.name = 'AWS Lambda - ' + function_name
    resource.aws_region = aws_region
    resource.aws_rh_id = rh.id
    resource.aws_function_name = function_name
    resource.save()

    return "", "", ""