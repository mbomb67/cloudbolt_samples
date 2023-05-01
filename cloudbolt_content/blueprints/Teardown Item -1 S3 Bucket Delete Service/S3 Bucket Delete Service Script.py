"""
Teardown service item action for AWS S3 Bucket blueprint.
"""
from common.methods import set_progress
from resourcehandlers.aws.models import AWSHandler
from resources.models import Resource

def run(**kwargs):
    resource = kwargs.pop('resources').first()
    
    bucket_name = resource.s3_bucket_name
    
    try:
        aws = AWSHandler.objects.get(id=resource.aws_rh_id)
        set_progress("This resource belongs to {}".format(aws))
    
        wrapper = aws.get_api_wrapper()
        wrapper.region_name = resource.s3_bucket_region
    
        set_progress('Connecting to Amazon S3...')
        conn = wrapper.get_boto3_resource(
            aws.serviceaccount,
            aws.servicepasswd,
            wrapper.region_name,
            service_name='s3'
        )
        bucket = conn.Bucket(bucket_name)
        set_progress(f"bucket {bucket}")
        # Code to execute when bucket is non-empty - STARTS HERE
        if bucket.get_available_subresources():
            bucket_versioning = conn.BucketVersioning(bucket_name)
            try:
                if bucket_versioning.status == "Enabled":
                    bucket.object_versions.delete()
                else:
                    bucket.objects.all().delete()
                
                # Code to execute when bucket is non-empty - ENDS HERE
                set_progress(f'Deleting S3 Bucket "{bucket_name}" and contents...')
                response = bucket.delete()
    
                response_status = response.get('ResponseMetadata').get('HTTPStatusCode')
                if response_status != 204:
                    return "FAILURE", f"Error while deleting {bucket_name}", f"{response}"
                    
            except Exception as e:
                pass
    except Exception as e:
                pass 
    Resource.objects.filter(parent_resource=resource).delete()
    

    return "SUCCESS", "Bucket Deleted successfully", ""