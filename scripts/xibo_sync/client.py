"""Xibo API client and upload/delete helpers used by the sync runner."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import List, Optional

import requests
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

from .ui import console


class XiboClient:
    """Minimal Xibo CMS API client used by the sync workflow."""

    def __init__(self, base_url: str, verify_tls: bool, timeout: int):
        """Store connection settings and create a reusable HTTP session.

        Args:
            base_url: Base URL of the Xibo CMS, without the trailing ``/api``.
            verify_tls: Whether TLS certificates should be verified.
            timeout: Request timeout in seconds.
        """
        self.base_url = base_url.rstrip("/")
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.session = requests.Session()
        self._oauth_client_id: Optional[str] = None
        self._oauth_client_secret: Optional[str] = None
        self._oauth_token_expires_at: Optional[float] = None

    def _api_url(self, path: str) -> str:
        """Build an API URL for a CMS endpoint path.

        Args:
            path: API path fragment starting with ``/``.

        Returns:
            Fully qualified CMS API URL.
        """
        return f"{self.base_url}/api{path}"

    def _token_url(self) -> str:
        """Return the OAuth token endpoint URL.

        Returns:
            Fully qualified OAuth token endpoint URL.
        """
        return f"{self.base_url}/api/authorize/access_token"

    @staticmethod
    def _extract_data(json_obj):
        """Unwrap Xibo API responses that place payloads under ``data``.

        Args:
            json_obj: Parsed JSON response from the CMS.

        Returns:
            The nested ``data`` payload when present, otherwise the original object.
        """
        if isinstance(json_obj, dict) and "data" in json_obj:
            return json_obj["data"]
        return json_obj

    def authenticate_oauth(self, client_id: str, client_secret: str) -> None:
        """Authenticate with OAuth2 client credentials and cache the token.

        Args:
            client_id: OAuth client identifier issued by Xibo.
            client_secret: OAuth client secret issued by Xibo.

        Raises:
            RuntimeError: Raised when the token request fails or the response is invalid.
        """
        self._oauth_client_id = client_id
        self._oauth_client_secret = client_secret

        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        r = self.session.post(self._token_url(), data=payload, timeout=self.timeout, verify=self.verify_tls)
        if r.status_code != 200:
            body_text = r.text
            try:
                body_json = r.json()
                body_text = str(body_json)
            except Exception:
                pass
            raise RuntimeError(f"OAuth token request failed ({r.status_code}): {body_text}")

        body = {}
        try:
            body = r.json()
        except Exception:
            pass

        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise RuntimeError(f"OAuth response missing access_token: {r.text}")

        expires_in = body.get("expires_in") if isinstance(body, dict) else None
        try:
            expires_val = float(expires_in) if expires_in is not None else 300.0
        except Exception:
            expires_val = 300.0

        self._oauth_token_expires_at = time.time() + expires_val - 60.0
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        logging.info("Authenticated via OAuth.")

    def _ensure_token_valid(self) -> None:
        """Refresh OAuth credentials when the token is missing or expired."""
        if ("Authorization" not in self.session.headers) and self._oauth_client_id and self._oauth_client_secret:
            logging.info("No OAuth token present - obtaining new token...")
            self.authenticate_oauth(self._oauth_client_id, self._oauth_client_secret)
            return

        if self._oauth_token_expires_at and time.time() >= self._oauth_token_expires_at:
            if self._oauth_client_id and self._oauth_client_secret:
                logging.info("OAuth token expired/near expiry - refreshing...")
                self.authenticate_oauth(self._oauth_client_id, self._oauth_client_secret)

    def _request(self, method: str, url: str, *, retry_on_401: bool = True, **kwargs):
        """Send a request and retry once after re-authentication on auth failures.

        Args:
            method: HTTP method name.
            url: Fully qualified request URL.
            retry_on_401: Whether to retry once after an authorization failure.
            **kwargs: Additional arguments forwarded to ``requests.Session.request``.

        Returns:
            The ``requests.Response`` object returned by the CMS.
        """
        self._ensure_token_valid()
        r = self.session.request(method, url, timeout=self.timeout, verify=self.verify_tls, **kwargs)
        if retry_on_401 and r.status_code in (401, 403) and self._oauth_client_id and self._oauth_client_secret:
            logging.warning("Request unauthorized (%s). Re-authenticating and retrying once...", r.status_code)
            self.authenticate_oauth(self._oauth_client_id, self._oauth_client_secret)
            r = self.session.request(method, url, timeout=self.timeout, verify=self.verify_tls, **kwargs)
        return r

    def health_check(self) -> None:
        """Verify that the CMS API is reachable and authenticated.

        Raises:
            RuntimeError: Raised when the CMS cannot be reached or authorized.
        """
        r = self._request(
            "GET",
            self._api_url("/library"),
            params={"start": 0, "length": 1},
        )
        if r.status_code in (401, 403):
            raise RuntimeError(
                f"API unauthorized ({r.status_code}). Set AUTH_MODE=oauth and provide CMS_CLIENT_ID/CMS_CLIENT_SECRET."
            )
        if r.status_code != 200:
            raise RuntimeError(f"API check failed ({r.status_code}): {r.text}")

    def list_library(self, managed_tag: Optional[str], folder_id: Optional[str]) -> List[dict]:
        """Fetch library items, optionally constrained by tag and folder.

        Args:
            managed_tag: Optional tag filter used to scope the library query.
            folder_id: Optional folder filter used to scope the library query.

        Returns:
            A list of library item dictionaries returned by Xibo.
        """
        logging.info("Step 2: Fetching CMS Library entries ...")

        items: List[dict] = []
        start = 0
        page_size = 1000

        while True:
            params = {"start": start, "length": page_size}
            if managed_tag:
                params["tags"] = managed_tag
                params["embed"] = "tags"
            if folder_id:
                params["folderId"] = folder_id

            r = self._request("GET", self._api_url("/library"), params=params)
            if r.status_code != 200:
                raise RuntimeError(f"Library list failed ({r.status_code}): {r.text}")

            data = self._extract_data(r.json())
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected library response format: {r.text}")

            items.extend(data)

            if len(data) < page_size:
                break
            start += page_size

        logging.info("CMS Library fetched: %d item(s).", len(items))
        return items

    def get_library_item(self, media_id: str) -> Optional[dict]:
        """Fetch a single library item by media ID.

        Args:
            media_id: Identifier of the media item to fetch.

        Returns:
            The matching media dictionary when Xibo returns one, otherwise ``None``.

        Raises:
            RuntimeError: Raised when the query fails or returns an unexpected format.
        """
        r = self._request(
            "GET",
            self._api_url("/library"),
            params={"mediaId": media_id},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Library item lookup failed for mediaId={media_id} ({r.status_code}): {r.text}")

        data = self._extract_data(r.json())
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            return data

        raise RuntimeError(f"Unexpected library item lookup format for mediaId={media_id}: {r.text}")

    def tag_media(self, media_id: str, tags: List[str]) -> None:
        """Attach one or more metadata tags to a media item in Xibo.

        Args:
            media_id: Identifier of the media item to update.
            tags: Tag values to attach to the media item.

        Raises:
            RuntimeError: Raised when the tag request fails.
        """
        clean_tags = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
        if not clean_tags:
            return

        r = self._request(
            "POST",
            self._api_url(f"/library/{media_id}/tag"),
            data=[("tag[]", tag) for tag in dict.fromkeys(clean_tags)],
        )
        if r.status_code != 200:
            raise RuntimeError(f"Tagging mediaId={media_id} failed ({r.status_code}): {r.text}")

    def upload_media(
        self,
        file_path: Path,
        name: Optional[str],
        folder_id: Optional[str],
        tags: List[str],
        preferred_field: str,
        dry_run: bool,
    ) -> Optional[dict]:
        """Upload one media file, trying supported multipart field names if needed.

        Args:
            file_path: Local file to upload.
            name: Display name to send to Xibo.
            folder_id: Optional target folder identifier.
            tags: Tags to attach to the uploaded media.
            preferred_field: Multipart field name to try first.
            dry_run: Whether to skip the actual upload.

        Returns:
            The created media object when the upload succeeds, otherwise ``None`` in dry-run mode.

        Raises:
            RuntimeError: Raised when all upload field variants fail.
        """
        logging.info("Uploading: %s", file_path.name)
        if dry_run:
            logging.info("[DRY_RUN] Would upload '%s'", file_path)
            return None

        url = self._api_url("/library")

        field_candidates = [preferred_field]
        for cand in ("files", "file", "media", "upload"):
            if cand not in field_candidates:
                field_candidates.append(cand)

        last_error = None

        with Progress(
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=None),
            TextColumn("[green]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TransferSpeedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress:
            for upload_field in field_candidates:
                self._ensure_token_valid()
                f = open(file_path, "rb")
                try:
                    fields = {
                        upload_field: (file_path.name, f, "application/octet-stream"),
                    }
                    if name:
                        fields["name"] = name
                    if folder_id:
                        fields["folderId"] = folder_id
                    if tags:
                        fields["tags"] = ",".join(dict.fromkeys(tags))

                    encoder = MultipartEncoder(fields=fields)
                    task_id = progress.add_task(f"Uploading {file_path.name}", total=encoder.len)

                    def _cb(monitor: MultipartEncoderMonitor):
                        progress.update(task_id, completed=monitor.bytes_read)

                    monitor = MultipartEncoderMonitor(encoder, _cb)
                    headers = {"Content-Type": monitor.content_type}

                    r = self._request(
                        "POST",
                        url,
                        data=monitor,
                        headers=headers,
                        retry_on_401=True,
                    )

                    if r.status_code in (200, 201):
                        payload = r.json()
                        data = self._extract_data(payload)
                        created = data[0] if isinstance(data, list) and data else data

                        if not created:
                            logging.warning("Upload succeeded but response has no media object: %s", payload)
                            return None

                        media_id = str(created.get("files")[0].get("mediaId") if created.get("files") else created.get("mediaId") or created.get("id") or "")

                        if not media_id:
                            raise RuntimeError(
                                f"Upload of '{file_path.name}' succeeded but Xibo did not return a mediaId, so the upload cannot be verified."
                            )

                        verified = self.get_library_item(media_id)
                        if not verified:
                            raise RuntimeError(
                                f"Upload of '{file_path.name}' was accepted, but mediaId={media_id} is not present in the Xibo library."
                            )

                        valid_flag = verified.get("valid")
                        if valid_flag not in (1, "1", True):
                            raise RuntimeError(
                                f"Upload of '{file_path.name}' returned mediaId={media_id}, but Xibo marked it invalid (valid={valid_flag!r}). The file may be unsupported or rejected."
                            )

                        self.tag_media(media_id, tags)

                        return verified

                    last_error = f"Upload attempt with field '{upload_field}' failed ({r.status_code}): {r.text}"
                    logging.warning(last_error)

                finally:
                    try:
                        f.close()
                    except Exception:
                        pass

        raise RuntimeError(last_error or "Upload failed (unknown reason)")

    def delete_media(self, media_id: str, dry_run: bool) -> None:
        """Delete one media item from Xibo, honoring dry-run mode.

        Args:
            media_id: Identifier of the media item to delete.
            dry_run: Whether to skip the actual delete.

        Raises:
            RuntimeError: Raised when the delete request fails.
        """
        logging.info("Deleting mediaId=%s ...", media_id)
        if dry_run:
            logging.info("[DRY_RUN] Would delete mediaId=%s", media_id)
            return

        r = self._request(
            "DELETE",
            self._api_url(f"/library/{media_id}"),
            data={"forceDelete": 1},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Delete mediaId={media_id} failed ({r.status_code}): {r.text}")

    def collect_now(self, display_group_id: str, dry_run: bool) -> None:
        """Trigger a player refresh for the configured display group.

        Args:
            display_group_id: Xibo display group to refresh.
            dry_run: Whether to skip the actual refresh call.

        Raises:
            RuntimeError: Raised when the collect-now request fails.
        """
        logging.info("Triggering Collect Now for displayGroupId=%s ...", display_group_id)
        if dry_run:
            logging.info("[DRY_RUN] Would call collectNow for displayGroupId=%s", display_group_id)
            return

        r = self._request(
            "POST",
            self._api_url(f"/displaygroup/{display_group_id}/action/collectNow"),
        )
        if r.status_code != 200:
            raise RuntimeError(f"collectNow failed ({r.status_code}): {r.text}")
