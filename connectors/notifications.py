"""
Email Notification Client — Sends alerts and reports via AWS SES.
Configured via NotificationConfig (injected from manifest).
"""

import boto3
import logging
from utils.decorators import monitor_and_log

logger = logging.getLogger(__name__)


class EmailNotificationClient:
    def __init__(self, notification_config, fallback_target_config=None):
        """
        Args:
            notification_config: NotificationConfig dataclass
            fallback_target_config: Optional TargetConfig to reuse S3 AWS creds if SES creds not set
        """
        self.config = notification_config
        self.sender = notification_config.sender_email
        self.recipient = notification_config.success_recipient_email
        self.failure_recipient = notification_config.failure_recipient_email

        # Use dedicated SES credentials, or fall back to S3 target creds
        ses_key = notification_config.ses_access_key_id
        ses_secret = notification_config.ses_secret_access_key
        ses_region = notification_config.aws_region

        if ses_key:
            use_key, use_secret, use_region = ses_key, ses_secret, ses_region or "ap-south-1"
        elif fallback_target_config:
            logger.warning("SES creds not set. Falling back to S3 target credentials.")
            use_key = fallback_target_config.aws_access_key_id
            use_secret = fallback_target_config.aws_secret_access_key
            use_region = ses_region or fallback_target_config.region
        else:
            logger.warning("No SES or fallback credentials configured. Notifications may fail.")
            use_key, use_secret, use_region = None, None, "ap-south-1"

        self.client = boto3.client(
            "ses",
            aws_access_key_id=use_key,
            aws_secret_access_key=use_secret,
            region_name=use_region,
        )

    @monitor_and_log(log_message="Failed to send notification email", reraise=False)
    def send_email(self, subject: str, html_body: str, override_recipient: str = None) -> bool:
        """Sends an HTML email."""
        dest_email = override_recipient or self.recipient

        if not self.sender or not dest_email:
            logger.warning("Notification Sender or Recipient not configured. Skipping.")
            return False
            
        # Handle multiple recipients (comma-separated string)
        to_addresses = [email.strip() for email in dest_email.split(",")] if isinstance(dest_email, str) else dest_email

        logger.info(f"Sending Notification to {to_addresses}: {subject}")
        response = self.client.send_email(
            Source=self.sender,
            Destination={"ToAddresses": to_addresses},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {"Html": {"Data": html_body, "Charset": "UTF-8"}},
            },
        )
        logger.info(f"Notification sent. MessageId: {response.get('MessageId')}")
        return True
