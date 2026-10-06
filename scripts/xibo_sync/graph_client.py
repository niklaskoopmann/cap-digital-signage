"""Minimal Microsoft Graph client for calendar and SharePoint downloads."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote
from typing import Any

import requests


class GraphClient:
    """Authenticate with MSAL and issue bounded Graph requests."""

    def __init__(
        self,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        base_url: str,
        timeout: int,
        *,
        msal_app=None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self._access_token: str | None = None
        if msal_app is None:
            import msal

            msal_app = msal.ConfidentialClientApplication(
                client_id,
                authority=f"https://login.microsoftonline.com/{tenant_id}",
                client_credential=client_secret,
            )
        self.msal_app = msal_app

    def authenticate(self) -> None:
        result = self.msal_app.acquire_token_for_client(
            scopes=["https://graph.microsoft.com/.default"]
        )
        token = result.get("access_token")
        if not token:
            error = result.get("error", "")
            description = result.get("error_description", "")
            raise RuntimeError(f"Microsoft Graph authentication failed: {error} {description}".strip())
        self._access_token = token

    def get_paged(
        self,
        path_or_url: str,
        params: dict[str, Any] | None = None,
    ) -> tuple[str | None, list[dict[str, Any]]]:
        if not self._access_token:
            raise RuntimeError("Microsoft Graph client is not authenticated")

        url = path_or_url if path_or_url.startswith(("http://", "https://")) else f"{self.base_url}/{path_or_url.lstrip('/')}"
        first_context: str | None = None
        items: list[dict[str, Any]] = []
        first_page = True
        while url:
            response = self.session.get(
                url,
                headers={"Authorization": f"Bearer {self._access_token}"},
                params=params if first_page else None,
                timeout=self.timeout,
            )
            if not 200 <= response.status_code < 300:
                raise RuntimeError(
                    f"Microsoft Graph GET failed with status {response.status_code}: {response.text}"
                )
            payload = response.json()
            if first_page:
                first_context = payload.get("@odata.context")
            items.extend(payload.get("value", []))
            url = payload.get("@odata.nextLink")
            first_page = False
        return first_context, items

    def download(self, item: dict[str, Any], site_id: str, dest: Path) -> None:
        item_name = str(item.get("name") or "(unnamed)")
        item_id = str(item.get("id") or "(unknown)")
        temp_path = dest.with_suffix(dest.suffix + ".part")
        download_url = item.get("@microsoft.graph.downloadUrl")
        if download_url:
            url = str(download_url)
            headers = {}
        else:
            url = (
                f"{self.base_url}/sites/{quote(site_id, safe='')}/drive/items/"
                f"{quote(item_id, safe='')}/content"
            )
            if not self._access_token:
                raise RuntimeError("Microsoft Graph client is not authenticated")
            headers = {"Authorization": f"Bearer {self._access_token}"}

        try:
            response = self.session.get(
                url,
                headers=headers,
                stream=True,
                timeout=self.timeout,
            )
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f"HTTP status {response.status_code}")
            with temp_path.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 64):
                    if chunk:
                        output.write(chunk)
            os.replace(temp_path, dest)
        except Exception as exc:
            temp_path.unlink(missing_ok=True)
            # requests exception text can contain the pre-authenticated download URL.
            reason = type(exc).__name__ if isinstance(exc, requests.RequestException) else str(exc)
            raise RuntimeError(
                f"Microsoft Graph download failed for {item_name} (id {item_id}): {reason}"
            ) from None
