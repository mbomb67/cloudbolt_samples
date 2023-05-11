import time
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

    job.set_progress('Stopping MySQL database instance {0}...'.format(mysql_instance_identifier))
    
    try:
        # fetch MySQL database instance
        mysql_rsp = client.describe_db_instances(DBInstanceIdentifier=mysql_instance_identifier)['DBInstances'][0]
    except Exception as err:
        raise RuntimeError(err)
    
    if mysql_rsp['DBInstanceStatus'] != "available":
        return "WARNING", f"MySQL database instance {mysql_instance_identifier} is not in available state, it may have already been stopped or in-process state.", ""
    
    try:
        mysql_rsp = client.stop_db_instance(
                        DBInstanceIdentifier=mysql_instance_identifier
                    )['DBInstance']
    
    except Exception as err:
        raise RuntimeError(err)
    
    if mysql_rsp['DBInstanceStatus'] != "stopped":
        while True:
            try:
                # fetch MySQL database instance
                mysql_rsp = client.describe_db_instances(DBInstanceIdentifier=mysql_instance_identifier)['DBInstances'][0]
            except Exception as err:
                break
            
            if mysql_rsp['DBInstanceStatus'] == "stopped":
                break
        
            time.sleep(60)
    
    resource.db_status = "stopped"
    resource.save()

    job.set_progress('MySQL database instance {0} stopped successfully.'.format(mysql_instance_identifier))

    return 'SUCCESS', f'MySQL database instance {mysql_instance_identifier} stopped successfully.', ''