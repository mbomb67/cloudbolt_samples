"""
This service action is intended to be used as a management action on the AWS
MySQL database Instance blueprint. Importing the blueprint from the CloudBolt Content
Library will automatically import this action.
"""

from common.methods import set_progress
from infrastructure.models import Environment
from resourcehandlers.aws.models import AWSHandler
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)


def get_aws_rh_and_region(resource):
    rh_aws_id = resource.aws_rh_id
    aws_region =  resource.aws_region
    rh_aws = None

    if rh_aws_id != "" or rh_aws_id is not None:
        rh_aws = AWSHandler.objects.get(id=rh_aws_id)

    return aws_region, rh_aws
    
def boto_instance_to_dict(boto_instance, region, handler):
    """
    Create a pared-down representation of an MySQL database from the full boto
    dictionary.
    """
    instance = {
        'aws_region': region,
        'aws_rh_id': handler.id,
        'db_identifier': boto_instance['DBInstanceIdentifier'],
        'db_engine': boto_instance['Engine'],
        'db_status': boto_instance['DBInstanceStatus'],
        'db_username': boto_instance['MasterUsername'],
        'db_publicly_accessible': boto_instance['PubliclyAccessible'],
        'db_availability_zone': boto_instance['AvailabilityZone']
    }

    # get subnet object
    subnet_group = boto_instance.get("DBSubnetGroup", {})

    # Endpoint may not be returned if networking is not set up yet
    endpoint = boto_instance.get('Endpoint', {})

    instance.update({'db_endpoint_address': endpoint.get('Address'), 
        'db_endpoint_port': endpoint.get('Port'), 
        'db_subnet_group': subnet_group.get("DBSubnetGroupName"),
        'db_subnets': [xx['SubnetIdentifier'] for xx in subnet_group.get("Subnets", [])]})

    logger.info(f"Updates MySQL database: {instance}")

    return instance
    
def run(job, resource, logger=None, **kwargs):
    # The Environment ID and MySQL database data dict were stored as attributes on
    # this service by a build action.
    mysql_instance_identifier = resource.db_identifier

    # get aws region and resource handler object
    region, aws, = get_aws_rh_and_region(resource)

    if aws is None or aws == "":
        return  "WARNING", f"MySQL database instance {mysql_instance_identifier} not found, it may have already been deleted", ""

    set_progress('Connecting to Amazon RDS')
    
    # get aws resource handler wrapper object
    wrapper = aws.get_api_wrapper()

    # initialize boto3 client
    client = wrapper.get_boto3_client(
                    'rds',
                    aws.serviceaccount,
                    aws.servicepasswd,
                    region
                )

    job.set_progress('Refreshing MySQL database connection info {0}...'.format(mysql_instance_identifier))

    # fetch MySQL database instance
    postgresql_rsp = client.describe_db_instances(DBInstanceIdentifier=mysql_instance_identifier)['DBInstances']

    if not postgresql_rsp:
        return  "WARNING", f"MySQL database instance {mysql_instance_identifier} not found, it may have already been deleted", ""


    # convert MySQL database instance to dict
    postgresql_instance = boto_instance_to_dict(postgresql_rsp[0], region, aws)

    for key, value in postgresql_instance.items():
        setattr(resource, key, value) # set custom field value

    resource.save()

    job.set_progress('MySQL database instance {0} updated.'.format(mysql_instance_identifier))

    return 'SUCCESS', f'MySQL database instance {mysql_instance_identifier} updated successfully.', ''