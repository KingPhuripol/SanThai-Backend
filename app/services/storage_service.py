import os
import boto3
import uuid
from botocore.exceptions import ClientError
from typing import Optional

def get_s3_client():
    return boto3.client(
        's3',
        endpoint_url=os.getenv('STORAGE_ENDPOINT_URL'),
        aws_access_key_id=os.getenv('AWS_ACCESS_KEY_ID'),
        aws_secret_access_key=os.getenv('AWS_SECRET_ACCESS_KEY'),
        region_name=os.getenv('AWS_REGION', 'ap-southeast-1')
    )

async def upload_file_to_s3(file_bytes: bytes, filename: str, content_type: str) -> Optional[str]:
    """
    Uploads a file to S3/Cloudflare R2 and returns the public URL.
    """
    bucket_name = os.getenv('STORAGE_BUCKET')
    public_url_prefix = os.getenv('STORAGE_PUBLIC_URL') # e.g. https://pub-xxxx.r2.dev

    if not bucket_name or not os.getenv('AWS_ACCESS_KEY_ID'):
        # Fallback to local if not configured
        return None
        
    s3_client = get_s3_client()
    
    # Generate unique filename
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "jpg"
    unique_filename = f"fabrics/{uuid.uuid4()}.{ext}"

    try:
        s3_client.put_object(
            Bucket=bucket_name,
            Key=unique_filename,
            Body=file_bytes,
            ContentType=content_type,
            # ACL='public-read'  # Some endpoints like R2 don't support ACL, better to use bucket policies or public URL prefix
        )
        
        if public_url_prefix:
            return f"{public_url_prefix}/{unique_filename}"
        
        # Fallback S3 URL
        endpoint = os.getenv('STORAGE_ENDPOINT_URL', f"https://s3.amazonaws.com")
        if "r2.cloudflarestorage.com" in endpoint:
             return f"{endpoint}/{bucket_name}/{unique_filename}"
        
        return f"https://{bucket_name}.s3.amazonaws.com/{unique_filename}"
    except ClientError as e:
        print(f"Error uploading to S3: {e}")
        return None
