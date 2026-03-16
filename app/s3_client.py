import boto3
from botocore.client import Config

from .config import settings


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT_URL,
        aws_access_key_id=settings.S3_ACCESS_KEY_ID,
        aws_secret_access_key=settings.S3_SECRET_ACCESS_KEY,
        region_name=settings.S3_REGION,
        config=Config(s3={"addressing_style": "path"}),
    )


def ensure_bucket_exists(bucket_name: str) -> None:
    client = get_s3_client()
    existing = client.list_buckets()
    names = {b["Name"] for b in existing.get("Buckets", [])}
    if bucket_name in names:
        return
    try:
        client.create_bucket(
            Bucket=bucket_name,
            CreateBucketConfiguration={"LocationConstraint": settings.S3_REGION},
        )
    except client.exceptions.BucketAlreadyOwnedByYou:
        return

