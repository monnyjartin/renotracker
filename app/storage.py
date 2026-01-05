import os
from typing import Optional

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from .config import settings


def s3_enabled() -> bool:
    return bool(settings.minio_endpoint and settings.minio_access_key and settings.minio_secret_key and settings.minio_bucket)


def _client(endpoint: str):
    """
    MinIO works best with path-style addressing when using IP:port endpoints.
    """
    return boto3.client(
        "s3",
        endpoint_url=endpoint.rstrip("/"),
        aws_access_key_id=settings.minio_access_key,
        aws_secret_access_key=settings.minio_secret_key,
        region_name=os.getenv("AWS_REGION", "us-east-1"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def get_s3_internal():
    # Used for bucket checks + uploads from inside Docker network
    return _client(settings.minio_endpoint)


def get_s3_public_for_signing():
    # Used ONLY for generating presigned URLs the browser will open
    # If not set, fall back to internal.
    endpoint = settings.minio_public_endpoint or settings.minio_endpoint
    return _client(endpoint)


def ensure_bucket_exists() -> None:
    if not s3_enabled():
        return

    s3 = get_s3_internal()
    bucket = settings.minio_bucket
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        # Create bucket if missing
        s3.create_bucket(Bucket=bucket)


def upload_bytes(key: str, data: bytes, content_type: Optional[str] = None) -> None:
    if not s3_enabled():
        return

    s3 = get_s3_internal()
    kwargs = {"Bucket": settings.minio_bucket, "Key": key, "Body": data}
    if content_type:
        kwargs["ContentType"] = content_type
    s3.put_object(**kwargs)


def presigned_get_url(key: str, expires_seconds: int = 3600) -> str:
    """
    Presigned URL must be signed for the same host/port the browser will call.
    """
    if not s3_enabled():
        raise RuntimeError("S3/MinIO not configured")

    s3 = get_s3_public_for_signing()
    return s3.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.minio_bucket, "Key": key},
        ExpiresIn=expires_seconds,
    )

def delete_object(key: str) -> None:
    if not s3_enabled():
        return
    s3 = get_s3()
    s3.delete_object(Bucket=settings.minio_bucket, Key=key)

