"""
Outlook Connector — Fetches emails and attachments via Microsoft Graph API.
Configured via SourceConfig + PipelineConfig (injected from manifest).
"""

import requests
from requests.adapters import HTTPAdapter
import logging
import base64
import os
from auth.azure_client import AzureClient
from utils.decorators import monitor_and_log, retry_with_backoff

logger = logging.getLogger(__name__)


class OutlookConnector:
    def __init__(self, source_config, pipeline_config):
        """
        Args:
            source_config: SourceConfig dataclass (mailbox, azure creds)
            pipeline_config: PipelineConfig dataclass (timeouts, retries, etc.)
        """
        self.source = source_config
        self.pipeline = pipeline_config

        # Initialize Azure auth with decrypted credentials
        self.auth_client = AzureClient(
            tenant_id=source_config.azure_tenant_id,
            client_id=source_config.azure_client_id,
            client_secret=source_config.azure_client_secret,
        )
        self.base_url = "https://graph.microsoft.com/v1.0"
        self.http_timeout = (pipeline_config.connection_timeout_sec, pipeline_config.read_timeout_sec)

        # Initialize session with connection pooling
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=10, pool_maxsize=10, pool_block=False)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

        logger.debug(f"OutlookConnector initialized for {source_config.mailbox_address}")

    def _get_headers(self):
        token = self.auth_client.acquire_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    @monitor_and_log(log_message="Error listing folders", default_return=None)
    def _get_folder_id(self, folder_name: str, parent_folder_id: str = None, depth: int = 0) -> str:
        """Recursively find folder ID by name (case-insensitive)."""
        max_depth = self.pipeline.max_folder_depth
        if depth > max_depth:
            logger.warning(f"Max folder search depth reached ({max_depth}) for '{folder_name}'")
            return None

        user_email = self.source.mailbox_address

        if parent_folder_id:
            endpoint = f"{self.base_url}/users/{user_email}/mailFolders/{parent_folder_id}/childFolders"
        else:
            endpoint = f"{self.base_url}/users/{user_email}/mailFolders"

        params = {"$top": self.pipeline.page_size}
        headers = self._get_headers()

        while endpoint:
            response = self.session.get(endpoint, headers=headers, params=params, timeout=self.http_timeout)
            response.raise_for_status()
            data = response.json()

            for folder in data.get("value", []):
                if folder["displayName"].lower() == folder_name.lower():
                    logger.info(f"Found target folder '{folder_name}' (ID: {folder['id']})")
                    return folder["id"]

                if folder.get("childFolderCount", 0) > 0:
                    child_id = self._get_folder_id(folder_name, parent_folder_id=folder["id"], depth=depth + 1)
                    if child_id:
                        return child_id

            endpoint = data.get("@odata.nextLink")
            params = None

        return None

    def fetch_unread_emails(self, subject_filter: str = None):
        """Fetch unread emails from the configured mailbox and folder."""
        user_email = self.source.mailbox_address
        target_folder = self.source.target_folder

        folder_id = self._get_folder_id(target_folder)
        if not folder_id:
            logger.error(f"Target folder '{target_folder}' not found.")
            return []

        endpoint = f"{self.base_url}/users/{user_email}/mailFolders/{folder_id}/messages"
        params = {
            "$filter": "isRead eq false",
            "$select": "id,receivedDateTime,subject,body,hasAttachments,internetMessageId,from",
            "$orderby": "receivedDateTime desc",
            "$top": self.pipeline.batch_size,
        }

        headers = self._get_headers()
        response = self.session.get(endpoint, headers=headers, params=params, timeout=self.http_timeout)
        response.raise_for_status()

        messages = response.json().get("value", [])
        if subject_filter:
            messages = [m for m in messages if subject_filter.lower() in m["subject"].lower()]

        logger.info(f"Fetched {len(messages)} unread emails from {target_folder} ({user_email})")
        return messages

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    @monitor_and_log(log_message="Error listing attachments", default_return=[])
    def list_attachments_metadata(self, message_id: str) -> list:
        """Fetch metadata for all file-type attachments in a message."""
        user_email = self.source.mailbox_address
        endpoint = f"{self.base_url}/users/{user_email}/messages/{message_id}/attachments"

        headers = self._get_headers()
        response = self.session.get(
            endpoint, headers=headers, params={"$select": "id,name,size,contentType"}, timeout=self.http_timeout
        )
        response.raise_for_status()

        attachments = []
        for att in response.json().get("value", []):
            if att.get("@odata.type") == "#microsoft.graph.fileAttachment":
                attachments.append({
                    "id": att["id"],
                    "name": att["name"],
                    "size": att["size"],
                    "content_type": att.get("contentType"),
                })
        return attachments

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    @monitor_and_log(log_message="Error downloading single attachment", default_return=None)
    def download_attachment(self, message_id: str, attachment_id: str, save_path: str) -> bool:
        """Download a specific attachment by ID to a local path."""
        user_email = self.source.mailbox_address
        endpoint = f"{self.base_url}/users/{user_email}/messages/{message_id}/attachments/{attachment_id}"

        headers = self._get_headers()
        response = self.session.get(endpoint, headers=headers, timeout=self.http_timeout)
        response.raise_for_status()
        data = response.json()

        if "contentBytes" not in data:
            logger.error(f"Attachment {attachment_id} has no contentBytes.")
            return False

        content_bytes = base64.b64decode(data["contentBytes"])
        with open(save_path, "wb") as f:
            f.write(content_bytes)

        logger.info(f"Downloaded attachment: {os.path.basename(save_path)} ({len(content_bytes)} bytes)")
        return True

    @monitor_and_log(log_message="Error marking email processed", reraise=True)
    def mark_email_processed(self, message_id: str):
        """Mark email as read (processed)."""
        user_email = self.source.mailbox_address
        endpoint = f"{self.base_url}/users/{user_email}/messages/{message_id}"

        headers = self._get_headers()
        response = self.session.patch(endpoint, headers=headers, json={"isRead": True}, timeout=self.http_timeout)
        response.raise_for_status()
        logger.info(f"Marked email {message_id} as read.")
