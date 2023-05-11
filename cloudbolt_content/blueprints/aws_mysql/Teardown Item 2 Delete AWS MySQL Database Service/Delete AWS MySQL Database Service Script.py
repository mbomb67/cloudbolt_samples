from common.methods import set_progress
from infrastructure.models import Environment
from resourcehandlers.aws.models import AWSHandler
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

def get_aws_rh_and_region(resource):
    rh_aws_id = resource.aws_rh_id
    aws_region =  resource.aws_region
    rh_aws = None

    if rh_aws_id is not None or rh_aws_id != "":
        rh_aws = AWSHandler.objects.filter(id=rh_aws_id).first()

    return aws_region, rh_aws

def run(job, logger=None, **kwargs):
    resource = kwargs.pop('resources').first()

    set_progress(f"MySQL database Delete plugin running for resource: {resource}")
    logger.info(f"MySQL database Delete plugin running for resource: {resource}")
    
    mysql_database_identifier = resource.db_identifier
    
    # get aws region and resource handler object
    region, aws, = get_aws_rh_and_region(resource)

    if aws is None or aws == "":
        return "WARNING", "", "Need a valid aws region to delete this database"

    set_progress('Connecting to Amazon MySQL Database')
    
    # get aws resource handler wrapper object
    wrapper = aws.get_api_wrapper()
    
    # initialize boto3 client
    client = wrapper.get_boto3_client(
            'rds',
            aws.serviceaccount,
            aws.servicepasswd,
            region
        )
    
    try:
        # verify MySQL database instance
        rds_resp = client.describe_db_instances(DBInstanceIdentifier=mysql_database_identifier)
    except Exception as err:
        if "DBInstanceNotFound" in str(err):
            return "WARNING", f"MySQL Database instance {mysql_database_identifier} not found, it may have already been deleted", ""
        raise RuntimeError(err)
    
    job.set_progress('Deleting MySQL Database {0}...'.format(mysql_database_identifier))
    
    # delete MySQL Database from AWS
    client.delete_db_instance(
        DBInstanceIdentifier=mysql_database_identifier,
        # AWS strongly recommends taking a final snapshot before deleting a DB.
        # To do so, either set this to False or let the user choose by making it
        # a runtime action input (in that case be sure to set the param type to
        # Boolean so users get a dropdown).
        SkipFinalSnapshot=True,
        DeleteAutomatedBackups=True,
    )
    
    job.set_progress(f"MySQL database {mysql_database_identifier} deleted successfully")
    
    return 'SUCCESS', f"MySQL database {mysql_database_identifier} deleted successfully", ''