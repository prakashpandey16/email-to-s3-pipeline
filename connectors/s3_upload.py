"""
S3 Connector — Uploads files to S3 bucket.
Configured via TargetConfig (injected from manifest).
"""

import boto3
import logging
import os
from botocore.config import Config
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig
from utils.decorators import monitor_and_log

logger = logging.getLogger(__name__)

# Tuned boto3 configuration for stability
S3_CLIENT_CONFIG = Config(
    read_timeout=120,
    connect_timeout=10,
    retries={"max_attempts": 3, "mode": "adaptive"},
)

# Multipart upload configuration
MULTIPART_THRESHOLD_MB = 50
MULTIPART_CHUNKSIZE_MB = 10

TRANSFER_CONFIG = TransferConfig(
    multipart_threshold=MULTIPART_THRESHOLD_MB * 1024 * 1024,
    multipart_chunksize=MULTIPART_CHUNKSIZE_MB * 1024 * 1024,
    max_concurrency=5,
    use_threads=True,
)


class S3Connector:
    def __init__(self, target_config):
        """
        Args:
            target_config: TargetConfig dataclass (bucket, region, aws creds)
        """
        self.client = boto3.client(
            "s3",
            aws_access_key_id=target_config.aws_access_key_id,
            aws_secret_access_key=target_config.aws_secret_access_key,
            region_name=target_config.region,
            config=S3_CLIENT_CONFIG,
        )
        self.bucket_name = target_config.bucket_name

    def file_exists(self, s3_key: str) -> bool:
        """Check if a file already exists in S3 at the given key."""
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=s3_key)
            return True
        except ClientError as e:
            if e.response["Error"]["Code"] == "404":
                return False
            logger.error(f"[S3_CHECK] Error checking {s3_key}: {e}")
            raise

    def validate_upload(self, s3_key: str, expected_size: int = None) -> dict:
        """Validate that a file was uploaded correctly."""
        try:
            response = self.client.head_object(Bucket=self.bucket_name, Key=s3_key)
            actual_size = response.get("ContentLength", 0)
            if expected_size and actual_size != expected_size:
                return {"valid": False, "details": f"Size mismatch: expected {expected_size}, got {actual_size}"}
            return {"valid": True, "details": f"Verified: {actual_size} bytes"}
        except ClientError as e:
            return {"valid": False, "details": f"Validation failed: {e}"}

    @monitor_and_log(log_message="Failed to upload file to S3", reraise=True)
    def upload_file(self, file_path: str, s3_key: str, original_filename: str = None,
                    row_count: int = None, skip_if_exists: bool = False) -> dict:
        """Uploads a file from disk to S3."""
        display_name = original_filename or os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)

        # Duplicate detection
        if skip_if_exists and self.file_exists(s3_key):
            logger.info(f"[S3_SKIP] File already exists, skipping: {s3_key}")
            return {"success": True, "skipped": True, "s3_key": s3_key, "size_bytes": file_size, "row_count": row_count}

        upload_type = "[MULTIPART]" if file_size_mb > MULTIPART_THRESHOLD_MB else ""
        row_info = f", {row_count} rows" if row_count else ""
        logger.info(f"[S3_UPLOAD] '{display_name}' ({file_size_mb:.2f}MB{row_info}) -> {s3_key} {upload_type}")

        self.client.upload_file(file_path, self.bucket_name, s3_key, Config=TRANSFER_CONFIG)

        validation = self.validate_upload(s3_key, file_size)
        if validation["valid"]:
            logger.info(f"[S3_UPLOAD] Successfully uploaded to {s3_key}")
        else:
            logger.warning(f"[S3_UPLOAD] Upload validation issue: {validation['details']}")

        return {
            "success": True, "skipped": False, "s3_key": s3_key,
            "size_bytes": file_size, "row_count": row_count, "validated": validation["valid"],
        }
