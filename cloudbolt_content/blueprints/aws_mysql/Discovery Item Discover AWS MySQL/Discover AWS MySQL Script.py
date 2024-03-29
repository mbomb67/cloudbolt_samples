import json
from common.methods import set_progress
from infrastructure.models import CustomField
from resourcehandlers.aws.models import AWSHandler
from botocore.client import ClientError
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

RESOURCE_IDENTIFIER = ['db_identifier', 'aws_region']

def get_or_create_custom_fields_as_needed():
    CustomField.objects.get_or_create(
        name='db_endpoint_address',
        defaults={
            "label": 'Endpoint Address',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint',
            "show_on_servers": True
        }
    )

    CustomField.objects.get_or_create(
        name='db_endpoint_port',
        defaults={
            "label": 'Endpoint Port',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint',
            "show_on_servers": True
        }
    )

    CustomField.objects.get_or_create(
        name='db_status',
        defaults={
            "label": 'Database Status',
            "type": 'STR',
            "description": 'PostgreSQl Database Status',
            "show_on_servers": True
        }
    )

    CustomField.objects.get_or_create(
        name='db_username',
        defaults={
            "label": 'Username',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint',
            "show_on_servers": True
        }
    )
    
    
def boto_instance_to_dict(boto_instance, region, handler):
    """
    Create a pared-down representation of an MySQL database from the full boto
    dictionary.
    """
    instance = {
        'name': boto_instance['DBInstanceIdentifier'],
        'aws_region': region,
        'aws_rh_id': handler.id,
        'db_identifier': boto_instance['DBInstanceIdentifier'],
        'db_engine': boto_instance['Engine'],
        'db_status': boto_instance['DBInstanceStatus'],
        'db_username': boto_instance['MasterUsername'],
        'db_publicly_accessible': boto_instance['PubliclyAccessible'],
        'db_availability_zone': boto_instance['AvailabilityZone'],
        'db_cluster_identifier': boto_instance.get("DBClusterIdentifier", "")
    }
    
    # get subnet object
    subnet_group = boto_instance.get("DBSubnetGroup", {})

    # Endpoint may not be returned if networking is not set up yet
    endpoint = boto_instance.get('Endpoint', {})
    
    instance.update({'db_endpoint_address': endpoint.get('Address'), 
        'db_endpoint_port': endpoint.get('Port'), 
        'db_subnet_group': subnet_group.get("DBSubnetGroupName"),
        'db_subnets': [xx['SubnetIdentifier'] for xx in subnet_group.get("Subnets", [])]})
    
    logger.info(f"Discovered {instance} MySQL database on AWS.")
    
    return instance


def discover_resources(**kwargs):
    set_progress(f"Started discovering MySQL database on AWS.")
    logger.info(f"Started discovering MySQL database on AWS.")
    
    # get or create custom fields
    get_or_create_custom_fields_as_needed()
    
    discovered_mysql = []
    
    for handler in AWSHandler.objects.all():
        try:
            wrapper = handler.get_api_wrapper()
            set_progress('Connecting to Amazon MySQL database for handler: {}'.format(handler))
        except Exception as e:
            set_progress(f"Could not get wrapper: {e}")
            continue

        for region in handler.current_regions():
            rds = wrapper.get_boto3_client(
                'rds',
                handler.serviceaccount,
                handler.servicepasswd,
                region
            )

            try:
                db_instances =  rds.describe_db_instances()['DBInstances']
            except ClientError as e:
                set_progress('AWS ClientError: {}'.format(e))
                continue
    
            
            for db_instance in db_instances:
                
                if db_instance['Engine'] != 'mysql':
                    continue
                    
                discovered_mysql.append(boto_instance_to_dict(db_instance, region, handler))
                        
    return discovered_mysql