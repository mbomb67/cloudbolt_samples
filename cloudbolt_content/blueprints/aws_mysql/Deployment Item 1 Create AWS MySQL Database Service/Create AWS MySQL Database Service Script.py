"""
Build service item action for AWS MySQL database blueprint.
"""
import re
import time
from common.methods import set_progress
from infrastructure.models import CustomField, Environment
from accounts.models import Group
from utilities.logger import ThreadLogger

logger = ThreadLogger(__name__)

def get_or_create_custom_fields_as_needed():
    CustomField.objects.get_or_create(
        name='aws_rh_id',
        defaults={
            "label": 'AWS RH ID',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )

    CustomField.objects.get_or_create(
        name='db_identifier',
        defaults={
            "label": 'AWS database identifier',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )
    
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
        name='db_availability_zone',
        defaults={
            "label": 'Availability Zone',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )

    CustomField.objects.get_or_create(
        name='db_publicly_accessible',
        defaults={
            "label": 'Publicly Accessible',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )
    
    CustomField.objects.get_or_create(
        name='db_engine',
        defaults={
            "label": 'Engine',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )
    
    CustomField.objects.get_or_create(
        name='db_status',
        defaults={
            "label": 'Status',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint',
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
    
    CustomField.objects.get_or_create(
        name='db_subnet_group',
        defaults={
            "label": 'Subnet group',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )

    CustomField.objects.get_or_create(
        name='db_subnets',
        defaults={
            "label": 'Subnets',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )
    
    CustomField.objects.get_or_create(
        name='aws_region',
        defaults={
            "label": 'Region',
            "type": 'STR',
            "description": 'Used by the AWS Databases blueprint'
        }
    )  

def get_boto3_service_client(env, service_name="rds"):
    """
    Return boto connection to the RDS in the specified environment's region.
    """
    # get aws resource handler object
    rh = env.resource_handler.cast()

    # get aws wrapper object
    wrapper = rh.get_api_wrapper()

    # get aws client object
    client = wrapper.get_boto3_client(service_name, rh.serviceaccount, rh.servicepasswd, env.aws_region)
    
    return client
    
    
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
    
    
def generate_options_for_aws_region(**kwargs):
    """
    Generate AWS region options
    """
    group_name = kwargs["group"]
    options = []
    
    try:
        group = Group.objects.get(name=group_name)
    except Exception as err:
        return options
    
    
    # fetch all group environment
    envs = group.get_available_environments()
    
    aws_envs= [env for env in envs if env.resource_handler.resource_technology.name == "Amazon Web Services"]
    
    if not aws_envs:
        return options

    # get boto3 client    
    client = get_boto3_service_client(aws_envs[0])
    
    # fetch all mysql supported regions
    rds_support_regions = [region['RegionName'] for region in client.describe_source_regions()['SourceRegions']]
    
    
    for env in aws_envs:
        if env.aws_region not in rds_support_regions:
            continue
        
        options.append((env.id, env.name))
    
    return sort_dropdown_options(options, is_reverse=True)


def generate_options_for_db_engine_version(control_value=None, **kwargs):
    """
    Generate MySQL Database Engine version options
    Dependency: MySQL Database Engine
    """
    options = []
    
    if control_value is None or control_value == "":
        return options
    
    env = Environment.objects.get(id=control_value)

    # get boto3 client
    client = get_boto3_service_client(env)
    
    version_rgx = '^\d(\.\d)*$'
    filters=[{'Name':'status','Values':['available']},{'Name':'engine-mode','Values':['provisioned']}]
    
    for engine in client.describe_db_engine_versions(Engine='mysql', IncludeAll=False, Filters=filters)['DBEngineVersions']:
        option_label = engine['DBEngineVersionDescription']
        
        if re.match(version_rgx, engine['EngineVersion']) and engine['EngineVersion'] not in engine['DBEngineVersionDescription']:
            option_label = "{0} : {1}".format(engine['DBEngineVersionDescription'], engine['EngineVersion'])
            
        options.append(("{0}/{1}".format(engine['EngineVersion'], env.id), option_label))
    
    return sort_dropdown_options(options, is_reverse=True)
    
def generate_options_for_instance_class(control_value=None, **kwargs):
    """
    Generate MySQL Database Engine Version instance class options
    Dependency: MySQL Database Engine Version
    """
    options = []
    
    if control_value is None or control_value == "":
        return options
    
    control_value = control_value.split("/")
    env = Environment.objects.get(id=control_value[1])

    # get boto3 client
    client = get_boto3_service_client(env)
    
    ins_cls_dict = {'xlarge': {'cpu': 4, 'storage': 32}, '2xlarge': {'cpu': 8, 'storage': 64}, '4xlarge': {'cpu': 16, 'storage': 128}, 
                    '8xlarge':{'cpu': 32, 'storage': 256}, '16xlarge': {'cpu': 64, 'storage': 512}, 'large': {'cpu': 2, 'storage': 16},
                    'small': {'cpu': 2, 'storage': 2}, 'medium': {'cpu': 2, 'storage':4}
                    }
    
    # fetch all db engine version instance classes
    instance_klasss = client.describe_orderable_db_instance_options(Engine='mysql', Vpc=True, 
                                    EngineVersion=control_value[0])['OrderableDBInstanceOptions']
    
    for instance_klass in instance_klasss:
        storage = None
        cpu = None
        st_dict = ins_cls_dict.get(instance_klass['DBInstanceClass'].split(".")[-1], None)

        if "MaxStorageSize" in instance_klass and st_dict is not None:  
            if instance_klass['MinStorageSize']  <= st_dict['storage'] <= instance_klass['MaxStorageSize']:
                storage = st_dict['storage']
                cpu = st_dict['cpu']
            else:
                storage = instance_klass['MinStorageSize']
                cpu = int(storage)//8 if int(storage)//8 > 1 else 2
                
        elif st_dict is not None:
            storage = st_dict['storage']
            cpu = st_dict['cpu']

        if cpu is None:
            continue

        key = "{0}$?{1}$?{2}".format(instance_klass['DBInstanceClass'], storage, instance_klass['StorageType'])
        name = "{0} ({1} GiB RAM,   {2} vCPUs, {3} Storage)".format(instance_klass['DBInstanceClass'],storage, cpu, 
                                                    instance_klass['StorageType'].capitalize())
        options.append((key, name))
    
    return sort_dropdown_options(options, is_reverse=True)


def boto_instance_to_dict(boto_instance, env, client):
    """
    Create a pared-down representation of an MySQL database from the full boto dictionary.
    """

    instance = {
        'name': boto_instance['DBInstanceIdentifier'],
        'aws_region': env.aws_region,
        'aws_rh_id': env.resource_handler.cast().id,
        'db_identifier': boto_instance['DBInstanceIdentifier'],
        'db_engine': boto_instance['Engine'],
        'db_status': boto_instance['DBInstanceStatus'],
        'db_username': boto_instance['MasterUsername'],
        'db_publicly_accessible': boto_instance['PubliclyAccessible'],
        'db_availability_zone': boto_instance.get("AvailabilityZone", ""),
    }
    
    # get subnet object
    subnet_group = boto_instance.get("DBSubnetGroup", {})

    # Endpoint may not be returned if networking is not set up yet
    endpoint = boto_instance.get('Endpoint', {})
    
    if not endpoint:
        time.sleep(10)
        
        # fetch MySQL database instance
        mysql_response = client.describe_db_instances(DBInstanceIdentifier=boto_instance['DBInstanceIdentifier'], 
                                    Filters=[{'Name':'engine','Values':[boto_instance['Engine']]}])['DBInstances']
        if mysql_response:
            endpoint = mysql_response[0].get('Endpoint', {})

    instance.update({'db_endpoint_address': endpoint.get('Address'), 
        'db_endpoint_port': endpoint.get('Port'), 
        'db_subnet_group': subnet_group.get("DBSubnetGroupName"),
        'db_subnets': [xx['SubnetIdentifier'] for xx in subnet_group.get("Subnets", [])]})
    
    logger.info(f'MySQL database {instance} created successfully.')

    return instance
    
def run(job, logger=None, **kwargs):
    set_progress('Creating AWS MySQL database...')
    logger.info('Creating AWS MySQL database...')
    
    resource = kwargs.pop('resources').first()
    
    # get or create custom fields
    get_or_create_custom_fields_as_needed()
    
    env = Environment.objects.get(id='{{ aws_region }}')

    db_username = '{{ db_username }}'
    db_password = '{{ db_password }}'
    db_identifier = '{{ db_identifier }}'
    db_engine_version = '{{ db_engine_version }}'.split("/")[0]
    instance_class_obj = '{{ instance_class }}'.split("$?")
    instance_class = instance_class_obj[0]
    allocated_storage = int(instance_class_obj[1])
    storage_type = instance_class_obj[2]
    
    # get boto3 client
    client = get_boto3_service_client(env)
    
    mysql_payload = dict(
        DBInstanceIdentifier=db_identifier,
        DBInstanceClass=instance_class,
        StorageType=storage_type,
        AutoMinorVersionUpgrade=False,
        CopyTagsToSnapshot=True,
        Engine='mysql',
        EngineVersion=db_engine_version,
        MasterUsername = db_username,
        MasterUserPassword = db_password,
        BackupRetentionPeriod = 7,
        StorageEncrypted = True,
        DeletionProtection = False,
        LicenseModel = "general-public-license",
        AllocatedStorage=allocated_storage,
        DBName=db_identifier
    )
    
    if storage_type == 'io1':
        mysql_payload['Iops'] = 1000
            
    set_progress('Create MySQL database "{}"'.format(db_identifier))
    
    
    try:
        mysql_response = client.create_db_instance(**mysql_payload)
    except Exception as err:
        if 'DBInstanceAlreadyExists' in str(err):
            return ("FAILURE", "Database already exists", "DB instance %s exists already" % db_identifier)
        raise
    
    # It takes awhile for the DB to be created and backed up.
    waiter = client.get_waiter('db_instance_available')
    waiter.config.max_attempts = 100  # default is 40 but oracle takes more time.
    waiter.wait(DBInstanceIdentifier=db_identifier)
    
    logger.info(f"MySQL instance response {mysql_response}")
    
    # convert boto model into cb MySQL dict
    mysql_instance_dict = boto_instance_to_dict(mysql_response['DBInstance'], env, client)
    

    for key, value in mysql_instance_dict.items():
        setattr(resource, key, value) # set custom field value

    resource.name = db_identifier
    resource.save()

    set_progress(f'MySQL database {db_identifier} created successfully.')
    
    return 'SUCCESS', f'MySQL database {db_identifier} created successfully.', ''