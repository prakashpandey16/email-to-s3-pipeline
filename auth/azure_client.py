"""
Azure AD Client — Acquires OAuth tokens for Microsoft Graph API.
Accepts credentials via constructor (injected from SourceConfig).
"""

import msal
import logging
import time

logger = logging.getLogger(__name__)


class AzureClient:
    _token_cache = {}  # Class-level cache

    def __init__(self, tenant_id: str, client_id: str, client_secret: str):
        """
        Args:
            tenant_id: Azure AD Tenant ID (decrypted)
            client_id: Azure App Registration Client ID (decrypted)
            client_secret: Azure App Client Secret (decrypted)
        """
        self.tenant_id = tenant_id
        self.client_id = client_id

        self.authority = f"https://login.microsoftonline.com/{tenant_id}"
        self.scope = ["https://graph.microsoft.com/.default"]

        self.app = msal.ConfidentialClientApplication(
            client_id,
            authority=self.authority,
            client_credential=client_secret,
        )

    def acquire_token(self) -> str:
        """Acquire token via Client Credentials flow with caching."""
        now = time.time()
        cached = self._token_cache.get("access_token")
        expiry = self._token_cache.get("expires_at", 0)

        if cached and now < expiry - 60:  # 60s buffer
            return cached

        logger.info("Acquiring new Azure AD token...")

        result = self.app.acquire_token_silent(self.scope, account=None)
        if not result:
            result = self.app.acquire_token_for_client(scopes=self.scope)

        if "access_token" in result:
            token = result["access_token"]
            expires_in = result.get("expires_in", 3599)

            self._token_cache["access_token"] = token
            self._token_cache["expires_at"] = now + expires_in

            return token
        else:
            error_description = result.get("error_description", "Unknown error")
            logger.error(f"Failed to acquire token: {error_description}")
            raise Exception(f"Could not acquire Azure token: {error_description}")
