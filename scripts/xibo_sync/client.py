"""Xibo API client and upload/delete helpers used by the sync runner."""

from __future__ import annotations

import logging
import io
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

MEDIA_LAYOUT_TAG_PREFIX = "xibo-sync-media:"


def media_layout_ownership_tag(media_id: str) -> str:
    """Return the exact ownership tag used for a media-created layout."""
    return f"{MEDIA_LAYOUT_TAG_PREFIX}{media_id}"


def _upload_media_id(created: dict) -> str:
    """Extract a media ID from the response shapes returned by Xibo uploads."""
    media_id = created.get("mediaId") or created.get("id")
    files = created.get("files")
    if not media_id and isinstance(files, list) and files:
        first_file = files[0]
        if isinstance(first_file, dict):
            media_id = first_file.get("mediaId") or first_file.get("id")
    return str(media_id) if media_id is not None else ""


def _layout_id_from_payload(payload: object) -> str:
    """Extract a layout ID from direct or nested Xibo layout responses."""
    if isinstance(payload, dict):
        direct_id = payload.get("layoutId") or payload.get("id")
        if direct_id:
            return str(direct_id)
        for key in ("layout", "data", "payload"):
            nested_id = _layout_id_from_payload(payload.get(key))
            if nested_id:
                return nested_id
    elif isinstance(payload, list):
        for item in payload:
            nested_id = _layout_id_from_payload(item)
            if nested_id:
                return nested_id
    return ""


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
        if not r.ok:
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
        if not r.ok:
            raise RuntimeError(f"API check failed ({r.status_code}): {r.text}")

    def get_dataset(self, name: str, code: Optional[str] = None) -> Optional[dict]:
        """Find a DataSet by name, optionally constrained by its code."""
        params = {"dataSet": name}
        if code:
            params["code"] = code
        r = self._request("GET", self._api_url("/dataset"), params=params)
        if not r.ok:
            raise RuntimeError(f"DataSet lookup failed ({r.status_code}): {r.text}")
        data = self._extract_data(r.json())
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected DataSet lookup format: {r.text}")
        return data[0] if data else None

    def create_dataset(self, name: str, code: Optional[str] = None) -> dict:
        """Create a local, non-real-time text DataSet."""
        payload = {
            "dataSet": name,
            "isRemote": 0,
            "isRealTime": 0,
            "dataConnectorSource": "none",
        }
        if code:
            payload["code"] = code
        r = self._request("POST", self._api_url("/dataset"), data=payload)
        if not r.ok:
            raise RuntimeError(f"DataSet creation failed ({r.status_code}): {r.text}")
        data = self._extract_data(r.json())
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected DataSet creation format: {r.text}")
        return data

    def list_dataset_columns(self, dataset_id: str) -> List[dict]:
        """List columns belonging to a DataSet."""
        columns: List[dict] = []
        start = 0
        page_size = 1000

        while True:
            r = self._request(
                "GET",
                self._api_url(f"/dataset/{dataset_id}/column"),
                params={"start": start, "length": page_size},
            )
            if not r.ok:
                raise RuntimeError(f"DataSet column lookup failed ({r.status_code}): {r.text}")
            data = self._extract_data(r.json())
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected DataSet column format: {r.text}")
            columns.extend(data)
            if len(data) < page_size:
                return columns
            start += page_size

    def create_dataset_column(self, dataset_id: str, heading: str, column_order: int) -> dict:
        """Create a standard text/value DataSet column."""
        payload = {
            "heading": heading,
            "columnOrder": column_order,
            "dataTypeId": 1,
            "dataSetColumnTypeId": 1,
            "showFilter": 0,
            "showSort": 0,
        }
        r = self._request("POST", self._api_url(f"/dataset/{dataset_id}/column"), data=payload)
        if not r.ok:
            raise RuntimeError(f"DataSet column creation failed ({r.status_code}): {r.text}")
        data = self._extract_data(r.json())
        if not isinstance(data, dict):
            raise RuntimeError(f"Unexpected DataSet column creation format: {r.text}")
        return data

    def import_dataset_csv(self, dataset_id: str, csv_content: bytes, column_ids: List[str]) -> None:
        """Replace all DataSet rows using a header-bearing CSV snapshot."""
        fields = {
            "files": ("office_calendar_events.csv", io.BytesIO(csv_content), "text/csv"),
            "overwrite": "1",
            "ignorefirstrow": "1",
        }
        fields.update({f"csvImport_{column_id}": str(index) for index, column_id in enumerate(column_ids, 1)})
        encoder = MultipartEncoder(fields=fields)
        r = self._request(
            "POST",
            self._api_url(f"/dataset/import/{dataset_id}"),
            data=encoder,
            headers={"Content-Type": encoder.content_type},
        )
        if not r.ok:
            raise RuntimeError(f"DataSet CSV import failed ({r.status_code}): {r.text}")

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
            if not r.ok:
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
        if not r.ok:
            raise RuntimeError(f"Library item lookup failed for mediaId={media_id} ({r.status_code}): {r.text}")

        data = self._extract_data(r.json())
        if isinstance(data, list):
            return data[0] if data else None
        if isinstance(data, dict):
            return data

        raise RuntimeError(f"Unexpected library item lookup format for mediaId={media_id}: {r.text}")

    def find_library_item_by_name(self, name: str) -> Optional[dict]:
        """Find the newest library item with an exact media name."""
        r = self._request(
            "GET",
            self._api_url("/library"),
            params={"media": name, "length": 10, "sortBy": "modifiedDt", "sortDir": "desc"},
        )
        if not r.ok:
            raise RuntimeError(f"Library name lookup failed for name={name!r} ({r.status_code}): {r.text}")

        data = self._extract_data(r.json())
        if not isinstance(data, list):
            return data if isinstance(data, dict) else None
        for item in data:
            if isinstance(item, dict) and str(item.get("name") or item.get("fileName") or "") == name:
                return item
        return None

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
        if not r.ok:
            raise RuntimeError(f"Tagging mediaId={media_id} failed ({r.status_code}): {r.text}")

    def tag_layout(self, layout_id: str, tags: List[str], dry_run: bool = False) -> None:
        """Attach one or more metadata tags to a layout in Xibo.

        Args:
            layout_id: Identifier of the layout to update.
            tags: Tag values to attach to the layout.
            dry_run: If True, log the action but do not execute the request.

        Raises:
            RuntimeError: Raised when the tag request fails.
        """
        clean_tags = [t.strip() for t in tags if isinstance(t, str) and t.strip()]
        if not clean_tags:
            return

        logging.info("Tagging layoutId=%s with %s ...", layout_id, clean_tags)
        if dry_run:
            logging.info("[DRY_RUN] Would tag layoutId=%s with %s", layout_id, clean_tags)
            return

        r = self._request(
            "POST",
            self._api_url(f"/layout/{layout_id}/tag"),
            data=[("tag[]", tag) for tag in dict.fromkeys(clean_tags)],
        )
        if not r.ok:
            raise RuntimeError(f"Tagging layoutId={layout_id} failed ({r.status_code}): {r.text}")

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

                        media_id = _upload_media_id(created)

                        if not media_id:
                            logging.warning(
                                "Upload response for '%s' did not include a mediaId; searching the library by name.",
                                file_path.name,
                            )
                            verified = self.find_library_item_by_name(file_path.name)
                            if not verified:
                                raise RuntimeError(
                                    f"Upload of '{file_path.name}' succeeded, but Xibo returned no mediaId "
                                    "and the uploaded item was not found by name."
                                )
                            media_id = _upload_media_id(verified)
                            if not media_id:
                                raise RuntimeError(
                                    f"Upload of '{file_path.name}' was found in Xibo, but its library record "
                                    "did not include a mediaId."
                                )

                        else:
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
        if not r.ok:
            raise RuntimeError(f"Delete mediaId={media_id} failed ({r.status_code}): {r.text}")

    @staticmethod
    def _tag_values(resource: dict) -> set[str]:
        tags = resource.get("tags", resource.get("tag", []))
        if isinstance(tags, str):
            return {value.strip() for value in tags.split(",") if value.strip()}
        if isinstance(tags, dict):
            tags = [tags]
        if isinstance(tags, list):
            values = set()
            for tag in tags:
                if isinstance(tag, dict):
                    value = tag.get("tag") or tag.get("name") or tag.get("value")
                else:
                    value = tag
                if value:
                    values.add(str(value).strip())
            return values
        return set()

    def list_layouts_by_ownership_tag(self, ownership_tag: str) -> List[dict]:
        """Find layouts carrying an exact sync ownership tag."""
        layouts: List[dict] = []
        start = 0
        while True:
            params = {"tags": ownership_tag, "embed": "tags", "length": 1000}
            if start:
                params["start"] = start
            r = self._request("GET", self._api_url("/layout"), params=params)
            if not r.ok:
                raise RuntimeError(f"Layout lookup for tag={ownership_tag!r} failed ({r.status_code}): {r.text}")
            data = self._extract_data(r.json())
            if isinstance(data, dict):
                data = data.get("layouts", data.get("rows", [data]))
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected layout lookup response for tag={ownership_tag!r}: {r.text}")
            layouts.extend(layout for layout in data if isinstance(layout, dict) and ownership_tag in self._tag_values(layout))
            if len(data) < 1000:
                return layouts
            start += 1000

    @staticmethod
    def _schedule_layout_id(event: dict) -> Optional[str]:
        """Extract a layout reference only when the event identifies one safely."""
        for key in ("layoutId", "layout_id", "campaignId"):
            if event.get(key) not in (None, ""):
                return str(event[key])
        for key in ("layout", "campaign"):
            nested = event.get(key)
            if isinstance(nested, dict):
                found = XiboClient._schedule_layout_id(nested)
                if found:
                    return found
        return None

    @staticmethod
    def _schedule_campaign_id(event: dict) -> Optional[str]:
        if event.get("campaignId") not in (None, ""):
            return str(event["campaignId"])
        campaign = event.get("campaign")
        if isinstance(campaign, dict) and campaign.get("campaignId") not in (None, ""):
            return str(campaign["campaignId"])
        return None

    @staticmethod
    def _explicit_schedule_layout_id(event: dict) -> Optional[str]:
        for key in ("layoutId", "layout_id"):
            if event.get(key) not in (None, ""):
                return str(event[key])
        layout = event.get("layout")
        if isinstance(layout, dict):
            value = layout.get("layoutId") or layout.get("id")
            if value not in (None, ""):
                return str(value)
        return None

    def list_schedule_events_for_layout(self, layout_id: str, campaign_id: Optional[str] = None) -> List[dict]:
        """List schedule events whose explicit layout reference matches ``layout_id``."""
        if not campaign_id:
            raise RuntimeError(f"Cannot find schedule events for layoutId={layout_id}: missing campaignId")
        events: List[dict] = []
        start = 0
        while True:
            params = {"campaignId": campaign_id, "eventTypeId": 1, "length": 1000}
            if start:
                params["start"] = start
            r = self._request("GET", self._api_url("/schedule"), params=params)
            if not r.ok:
                raise RuntimeError(f"Schedule lookup for layoutId={layout_id} failed ({r.status_code}): {r.text}")
            data = self._extract_data(r.json())
            if isinstance(data, dict):
                data = data.get("schedule", data.get("events", data.get("rows", [data])))
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected schedule response for layoutId={layout_id}: {r.text}")
            events.extend(
                event for event in data
                if isinstance(event, dict)
                and self._schedule_campaign_id(event) == str(campaign_id)
                and self._explicit_schedule_layout_id(event) == str(layout_id)
            )
            if len(data) < 1000:
                return events
            start += 1000

    def delete_schedule_event(self, event_id: str, dry_run: bool = False) -> None:
        """Delete one schedule event, honoring dry-run mode."""
        logging.info("Deleting schedule eventId=%s ...", event_id)
        if dry_run:
            logging.info("[DRY_RUN] Would delete schedule eventId=%s", event_id)
            return
        r = self._request("DELETE", self._api_url(f"/schedule/{event_id}"))
        if not r.ok:
            raise RuntimeError(f"Delete schedule eventId={event_id} failed ({r.status_code}): {r.text}")

    def unassign_layout_from_known_displaygroups(
        self, display_group_ids: List[str], layout_id: str, dry_run: bool = False
    ) -> None:
        """Unassign a layout from the display group(s) this sync tool itself manages.

        Xibo 4.4.2's ``GET /displaygroup`` response never exposes a layout
        membership field (no ``layouts``/``layout``/``layoutIds`` key, and
        ``embed=layouts`` has no effect), so there is no supported way to
        discover *every* display group containing a layout. This sync tool
        only ever assigns layouts to the configured ``DISPLAY_GROUP_ID``
        (see ``assign_layouts_to_displaygroup``), so cleanup only needs to
        remove that same, known assignment. Unassigning a layout that was
        never assigned to a group is a no-op success.
        """
        for display_group_id in display_group_ids:
            if not display_group_id:
                continue
            self.unassign_layout_from_displaygroup(display_group_id, layout_id, dry_run=dry_run)

    def unassign_layout_from_displaygroup(self, display_group_id: str, layout_id: str, dry_run: bool = False) -> None:
        """Remove one layout from a display group, honoring dry-run mode.

        A 404 response means the layout was already not assigned to this
        group, which is treated as a successful no-op rather than an error.
        """
        logging.info("Unassigning layoutId=%s from displayGroupId=%s ...", layout_id, display_group_id)
        if dry_run:
            logging.info("[DRY_RUN] Would unassign layoutId=%s from displayGroupId=%s", layout_id, display_group_id)
            return
        r = self._request(
            "POST",
            self._api_url(f"/displaygroup/{display_group_id}/layout/unassign"),
            data=[("layoutId[]", str(layout_id))],
        )
        if r.status_code == 404:
            logging.info(
                "layoutId=%s was already not assigned to displayGroupId=%s", layout_id, display_group_id
            )
            return
        if not r.ok:
            raise RuntimeError(
                f"Unassign layoutId={layout_id} from displayGroupId={display_group_id} failed "
                f"({r.status_code}): {r.text}"
            )

    def delete_layout(self, layout_id: str, dry_run: bool = False) -> None:
        """Delete one layout, honoring dry-run mode."""
        logging.info("Deleting layoutId=%s ...", layout_id)
        if dry_run:
            logging.info("[DRY_RUN] Would delete layoutId=%s", layout_id)
            return
        r = self._request("DELETE", self._api_url(f"/layout/{layout_id}"))
        if not r.ok:
            raise RuntimeError(f"Delete layoutId={layout_id} failed ({r.status_code}): {r.text}")


    @staticmethod
    def _is_draft_layout(layout: dict) -> bool:
        status = layout.get("publishedStatusId")
        if str(status) == "2":
            return True
        return str(layout.get("publishedStatus") or "").strip().lower() == "draft"

    def _list_drafts_for_layout(self, layout_id: str) -> List[dict]:
        """Return draft records whose parent is ``layout_id`` with their tags."""
        r = self._request(
            "GET",
            self._api_url("/layout"),
            params={
                "parentId": layout_id,
                "showDrafts": 1,
                "publishedStatusId": 2,
                "embed": "tags",
                "length": 1000,
            },
        )
        if not r.ok:
            raise RuntimeError(f"Draft lookup for layoutId={layout_id} failed ({r.status_code}): {r.text}")
        data = self._extract_data(r.json())
        if isinstance(data, dict):
            data = data.get("layouts", data.get("rows", [data]))
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected draft lookup response for layoutId={layout_id}: {r.text}")
        return [draft for draft in data if isinstance(draft, dict) and self._is_draft_layout(draft)]

    def discard_layout_draft(self, draft_layout_id: str, dry_run: bool = False) -> None:
        """Discard one verified draft, restoring its published parent layout."""
        logging.info("Discarding verified draft layoutId=%s ...", draft_layout_id)
        if dry_run:
            logging.info("[DRY_RUN] Would discard verified draft layoutId=%s", draft_layout_id)
            return
        r = self._request("PUT", self._api_url(f"/layout/discard/{draft_layout_id}"))
        if not r.ok:
            raise RuntimeError(f"Discard draft layoutId={draft_layout_id} failed ({r.status_code}): {r.text}")

    def _resolve_layout_for_safe_deletion(self, layout: dict, ownership_tag: str) -> str:
        """Return the canonical layout ID, refusing cleanup while a draft exists."""
        source_id = str(layout.get("layoutId") or layout.get("id") or "")
        if not source_id:
            raise RuntimeError(f"Cannot resolve managed layout draft: missing layoutId in {layout}")

        if self._is_draft_layout(layout):
            parent_id = str(layout.get("parentId") or "")
            if not parent_id or parent_id == source_id:
                raise RuntimeError(
                    f"Cannot safely recover locked layoutId={source_id}: draft has no canonical parentId; "
                    f"ownershipTag={ownership_tag!r}. Retain media and resolve it in Xibo."
                )
            raise RuntimeError(
                f"Cannot safely clean canonical layoutId={parent_id} while active draft layoutId={source_id} exists: "
                "the Xibo API does not expose a reliable checkout owner. Retain media and resolve the draft in Xibo."
            )

        drafts = self._list_drafts_for_layout(source_id)
        if not drafts:
            return source_id

        draft_ids = [str(draft.get("layoutId") or draft.get("id") or "?") for draft in drafts]
        raise RuntimeError(
            f"Cannot safely clean canonical layoutId={source_id} while active draft layoutId(s)={draft_ids} exist: "
            "the Xibo API does not expose a reliable checkout owner. Retain media and resolve the draft in Xibo."
        )

    def cleanup_layout_for_media(
        self,
        layout: dict,
        media_id: str,
        ownership_tag: str,
        display_group_ids: Optional[List[str]] = None,
        dry_run: bool = False,
    ) -> None:
        """Remove one verified owned layout and its dependencies in order.

        An active draft always blocks cleanup because the bundled Xibo API does
        not expose a reliable checkout-owner identity. This leaves the media
        intact for an operator-assisted retry without risking another user's work.

        ``display_group_ids`` should be the display group(s) this sync tool is
        configured to assign layouts to; Xibo 4.4.2 exposes no API to discover
        every group containing a layout, so only known, sync-made assignments
        are unassigned (see ``unassign_layout_from_known_displaygroups``).
        """
        layout_id = str(layout.get("layoutId") or layout.get("id") or "")
        if not layout_id:
            raise RuntimeError(f"Managed layout cleanup for mediaId={media_id} has no layoutId: {layout}")
        if ownership_tag not in self._tag_values(layout):
            raise RuntimeError(
                f"Refusing layoutId={layout_id} cleanup for mediaId={media_id}: ownership tag {ownership_tag!r} is absent"
            )

        canonical_layout_id = self._resolve_layout_for_safe_deletion(layout, ownership_tag)
        campaign_id = layout.get("campaignId") or layout.get("layoutCampaignId")
        events = self.list_schedule_events_for_layout(canonical_layout_id, str(campaign_id) if campaign_id else None)
        for event in events:
            event_id = str(event.get("eventId") or event.get("scheduleId") or event.get("id") or "")
            if not event_id:
                raise RuntimeError(f"Managed layout cleanup layoutId={canonical_layout_id} found schedule event without an ID: {event}")
            self.delete_schedule_event(event_id, dry_run=dry_run)

        self.unassign_layout_from_known_displaygroups(
            display_group_ids or [], canonical_layout_id, dry_run=dry_run
        )

        self.delete_layout(canonical_layout_id, dry_run=dry_run)

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
        if not r.ok:
            raise RuntimeError(f"collectNow failed ({r.status_code}): {r.text}")

    def list_resolutions(self, enabled: Optional[int] = 1) -> List[dict]:
        """Fetch layout resolutions available to the current user.

        Args:
            enabled: Optional enabled flag filter passed through to the CMS.

        Returns:
            A list of resolution dictionaries returned by Xibo.
        """
        params = {"sortBy": "width", "sortDir": "desc"}
        if enabled is not None:
            params["enabled"] = enabled

        r = self._request("GET", self._api_url("/resolution"), params=params)
        if not r.ok:
            raise RuntimeError(f"Resolution list failed ({r.status_code}): {r.text}")

        data = self._extract_data(r.json())
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected resolution response format: {r.text}")
        return data

    @staticmethod
    def _coerce_int(value) -> Optional[int]:
        """Best-effort conversion to int for API payload helpers."""
        if value in (None, ""):
            return None
        try:
            return int(value)
        except Exception:
            return None

    def _select_resolution_id(
        self,
        resolution_id: Optional[int],
        media_width: Optional[int],
        media_height: Optional[int],
    ) -> Optional[int]:
        """Choose a CMS resolution for a fullscreen layout.

        If a caller provides ``resolution_id``, that value wins. Otherwise the
        best enabled resolution is chosen using the media aspect ratio when it is
        available, falling back to the first enabled resolution returned by the CMS.
        """
        if resolution_id is not None:
            return resolution_id

        resolutions = self.list_resolutions(enabled=1)
        if not resolutions:
            return None

        if media_width and media_height:
            media_ratio = media_width / media_height
            media_area = media_width * media_height

            def _score(resolution: dict):
                width = self._coerce_int(resolution.get("width") or resolution.get("designerWidth"))
                height = self._coerce_int(resolution.get("height") or resolution.get("designerHeight"))
                if not width or not height:
                    return (float("inf"), float("inf"))
                ratio_delta = abs((width / height) - media_ratio)
                area_delta = abs((width * height) - media_area) / max(media_area, 1)
                return (ratio_delta, area_delta)

            best = min(resolutions, key=_score)
        else:
            best = resolutions[0]

        return self._coerce_int(best.get("resolutionId"))

    def create_fullscreen_layout(
        self,
        media: dict | str,
        media_type: str = "media",
        resolution_id: Optional[int] = None,
        background_color: Optional[str] = None,
        layout_duration: Optional[int] = None,
        dry_run: bool = False,
    ) -> Optional[dict]:
        """Create a stored full-screen layout for the provided media.

        The CMS fullscreen convenience endpoint does not persist layouts reliably,
        so this helper creates the layout with POST /layout and then sets the media
        as the background image.
        """
        if isinstance(media, dict):
            media_id = str(media.get("mediaId") or media.get("id") or "")
            media_name = str(
                media.get("name")
                or media.get("fileName")
                or media.get("originalFileName")
                or media_id
            )
            media_width = self._coerce_int(media.get("width") or media.get("imageWidth"))
            media_height = self._coerce_int(media.get("height") or media.get("imageHeight"))
        else:
            media_id = str(media)
            media_name = media_id
            media_width = None
            media_height = None

        logging.info("Creating full-screen layout for mediaId=%s ...", media_id)
        if dry_run:
            logging.info("[DRY_RUN] Would create full-screen layout for mediaId=%s", media_id)
            return None

        chosen_resolution_id = self._select_resolution_id(
            resolution_id=resolution_id,
            media_width=media_width,
            media_height=media_height,
        )
        if chosen_resolution_id is None:
            raise RuntimeError(
                "Unable to determine a layout resolution. Provide resolutionId or configure at least one enabled resolution in Xibo."
            )

        create_payload = {
            "name": f"{media_name} fullscreen",
            "resolutionId": chosen_resolution_id,
        }

        r = self._request("POST", self._api_url("/layout"), data=create_payload)
        if not r.ok:
            raise RuntimeError(f"Create fullscreen layout failed ({r.status_code}): {r.text}")

        payload = self._extract_data(r.json())

        if isinstance(payload, list):
            payload = payload[0] if payload else None

        if not isinstance(payload, dict):
            raise RuntimeError(f"Create fullscreen layout returned unexpected payload: {r.text}")

        layout_id = self._coerce_int(payload.get("layoutId") or payload.get("id"))
        if not layout_id:
            raise RuntimeError(f"Create fullscreen layout response missing layoutId: {r.text}")

        logging.info(
            "Created layoutId=%s (publishedStatus=%s, parentId=%s, isLocked=%s)",
            layout_id,
            payload.get("publishedStatus"),
            payload.get("parentId"),
            payload.get("isLocked"),
        )

        background_payload = {
            "backgroundColor": background_color or "#000",
            "backgroundzIndex": 1,
            "backgroundImageId": int(media_id),
            "resolutionId": chosen_resolution_id,
        }

        def _set_background(target_layout_id: int):
            return self._request(
                "PUT",
                self._api_url(f"/layout/background/{target_layout_id}"),
                data=background_payload,
            )

        r = _set_background(layout_id)

        if not r.ok and r.status_code == 422 and "checkout" in r.text.lower():
            # A freshly created layout is not always immediately editable - Xibo
            # requires an explicit checkout before the background can be set. Only
            # attempt checkout here (rather than unconditionally, which can needlessly
            # collide with a layout that's already editable) since we now know it's
            # required.
            checkout_r = self._request("PUT", self._api_url(f"/layout/checkout/{layout_id}"))
            if not checkout_r.ok:
                already_checked_out = False
                if checkout_r.status_code == 422:
                    try:
                        already_checked_out = "already checked out" in checkout_r.json().get("message", "").lower()
                    except (ValueError, AttributeError):
                        already_checked_out = False
                if not already_checked_out:
                    raise RuntimeError(f"Checkout fullscreen layout failed ({checkout_r.status_code}): {checkout_r.text}")

                logging.info("Layout already checked out; discarding stale checkout and retrying...")
                discard_r = self._request("PUT", self._api_url(f"/layout/discard/{layout_id}"))
                if not discard_r.ok:
                    logging.warning(
                        "Discard stale checkout failed (%s), retrying checkout anyway...",
                        discard_r.status_code,
                    )
                checkout_r = self._request("PUT", self._api_url(f"/layout/checkout/{layout_id}"))
                if not checkout_r.ok:
                    raise RuntimeError(f"Checkout fullscreen layout failed after discard ({checkout_r.status_code}): {checkout_r.text}")

            # Checkout can create a new draft row with its own layoutId (the original
            # becomes the published parent) - use the checked-out id for the retry.
            checkout_data = self._extract_data(checkout_r.json())
            if isinstance(checkout_data, list):
                checkout_data = checkout_data[0] if checkout_data else None
            if isinstance(checkout_data, dict):
                payload = checkout_data
                layout_id = self._coerce_int(payload.get("layoutId") or payload.get("id")) or layout_id

            r = _set_background(layout_id)

        if not r.ok:
            raise RuntimeError(f"Set fullscreen layout background failed ({r.status_code}): {r.text}")

        data = self._extract_data(r.json())

        # Normalize to a single dict representing the created layout when possible.
        if isinstance(data, list) and data:
            if isinstance(data[0], dict):
                return {**payload, **data[0]}
            return payload
        if isinstance(data, dict):
            return data
        return payload

    def get_layout_by_name(self, name: str) -> Optional[dict]:
        """Search for a layout by name and return an exact, unambiguous match."""
        if not name:
            return None

        r = self._request("GET", self._api_url("/layout"), params={"layout": name, "length": 1000})
        if not r.ok:
            raise RuntimeError(f"Layout search failed ({r.status_code}): {r.text}")

        data = self._extract_data(r.json())
        if isinstance(data, dict):
            data = data.get("layouts", data.get("rows", [data]))
        if isinstance(data, list):
            matches = [
                item for item in data
                if isinstance(item, dict)
                and str(item.get("name") or item.get("layout") or "") == name
            ]
            if len(matches) > 1:
                raise RuntimeError(f"Layout search for exact name {name!r} was ambiguous ({len(matches)} matches)")
            return matches[0] if matches else None
        return None

    def get_draft_layout_id(self, layout_id: str) -> Optional[str]:
        """Find the draft record associated with a layout ID, if one exists."""
        r = self._request(
            "GET",
            self._api_url("/layout"),
            params={"layoutId": layout_id, "showDrafts": 1, "publishedStatusId": 2, "length": 1},
        )
        if not r.ok:
            return None
        return _layout_id_from_payload(self._extract_data(r.json())) or None

    def get_layout_by_id(self, layout_id: str) -> Optional[dict]:
        """Fetch one layout by ID, including drafts and tags, or ``None`` if missing."""
        r = self._request(
            "GET",
            self._api_url("/layout"),
            params={"layoutId": layout_id, "showDrafts": 1, "embed": "tags", "length": 1},
        )
        if not r.ok:
            return None
        data = self._extract_data(r.json())
        if isinstance(data, dict):
            data = data.get("layouts", data.get("rows", [data]))
        if isinstance(data, list) and data and isinstance(data[0], dict):
            return data[0]
        return None

    def publish_layout(self, layout_id: str, publish_now: bool = True, dry_run: bool = False) -> None:
        """Publish a layout so that players can play the new version.

        Calls PUT /layout/publish/{layoutId} with optional publishNow flag.
        """
        logging.info("Publishing layoutId=%s ...", layout_id)
        if dry_run:
            logging.info("[DRY_RUN] Would publish layoutId=%s", layout_id)
            return

        data = {"publishNow": 1 if publish_now else 0}
        r = self._request("PUT", self._api_url(f"/layout/publish/{layout_id}"), data=data)
        if not r.ok:
            raise RuntimeError(f"Publish layoutId={layout_id} failed ({r.status_code}): {r.text}")

    def assign_layouts_to_displaygroup(self, display_group_id: str, layout_ids: List[str], dry_run: bool = False) -> None:
        """Assign one or more layouts to a display group.

        Uses POST /displaygroup/{displayGroupId}/layout/assign with repeated form fields.
        """
        logging.info("Assigning layouts %s to displayGroupId=%s ...", layout_ids, display_group_id)
        if dry_run:
            logging.info("[DRY_RUN] Would assign layouts %s to displayGroupId=%s", layout_ids, display_group_id)
            return

        # Form-encode repeated layoutId[] values
        data = [("layoutId[]", str(lid)) for lid in layout_ids]
        r = self._request("POST", self._api_url(f"/displaygroup/{display_group_id}/layout/assign"), data=data)
        if not r.ok:
            raise RuntimeError(f"Assign layouts to displayGroupId={display_group_id} failed ({r.status_code}): {r.text}")

    def upload_html_package(
        self,
        file_path: Path,
        tags: List[str],
        dry_run: bool,
    ) -> Optional[dict]:
        """Upload an ``.htz`` HTML package file to the Xibo library.

        Delegates to :meth:`upload_media` which handles field-name fallback,
        progress display, verification, and tagging.

        Args:
            file_path: Local ``.htz`` file to upload.
            tags: Tags to attach to the uploaded media item.
            dry_run: Whether to skip the actual upload.

        Returns:
            The verified media dictionary when the upload succeeds, otherwise
            ``None`` in dry-run mode.

        Raises:
            RuntimeError: Raised when the upload or verification fails.
        """
        logging.info("Uploading HTML package: %s", file_path.name)
        return self.upload_media(
            file_path=file_path,
            name=file_path.name,
            folder_id=None,
            tags=tags,
            preferred_field="files",
            dry_run=dry_run,
        )

    def get_or_create_calendar_layout(
        self,
        layout_name: str,
        resolution_id: Optional[int],
        dry_run: bool,
    ) -> Optional[dict]:
        """Return an existing layout by exact name or create a new 1080p layout.

        Args:
            layout_name: Exact display name for the layout.
            resolution_id: Optional explicit resolution ID. When ``None``, the
                best 1920×1080 resolution is selected automatically.
            dry_run: Whether to skip creation when the layout does not exist.

        Returns:
            The layout dictionary when found or created, otherwise ``None`` in
            dry-run mode.

        Raises:
            RuntimeError: Raised when the layout search or creation fails.
        """
        existing = self.get_layout_by_name(layout_name)
        if existing is not None:
            existing_id = existing.get("layoutId") or existing.get("id") or ""
            logging.info("Found existing layout '%s' (id=%s)", layout_name, existing_id)
            return existing

        logging.info("Layout '%s' not found; creating ...", layout_name)
        if dry_run:
            logging.info("[DRY_RUN] Would create layout '%s'", layout_name)
            return None

        chosen_resolution_id = self._select_resolution_id(resolution_id, 1920, 1080)
        if chosen_resolution_id is None:
            raise RuntimeError(
                "Unable to determine a layout resolution for calendar layout. "
                "Configure at least one enabled resolution in Xibo."
            )

        payload = {"name": layout_name, "resolutionId": chosen_resolution_id}
        r = self._request("POST", self._api_url("/layout"), data=payload)
        if not r.ok:
            raise RuntimeError(f"Create calendar layout '{layout_name}' failed ({r.status_code}): {r.text}")

        data = self._extract_data(r.json())
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict):
            raise RuntimeError(f"Create calendar layout returned unexpected payload: {r.text}")

        logging.info(
            "Created layout '%s' (layoutId=%s)",
            layout_name,
            data.get("layoutId") or data.get("id"),
        )
        return data

    def assign_html_package_to_layout(
        self,
        layout_id: str,
        media_id: str,
        dry_run: bool,
    ) -> None:
        """Assign an uploaded HTML package to the first region of a layout.

        Uses ``GET /layout?layoutId={layoutId}`` to discover the first region's playlist,
        then ``POST /playlist/library/assign/{playlistId}`` to attach the media.

        # NOTE: requires verification — the playlist/library/assign endpoint
        # and the region/playlist discovery path have not been smoke-tested
        # against a live Xibo 4.x CMS in this repository. Verify against your
        # target CMS version before relying on this in production.

        Args:
            layout_id: ID of the target layout.
            media_id: ID of the uploaded HTML package media item.
            dry_run: Whether to skip the actual assignment.

        Raises:
            RuntimeError: Raised when the layout has no regions, or when
                either API call fails.
        """
        logging.info(
            "Assigning HTML package mediaId=%s to layoutId=%s ...", media_id, layout_id
        )
        if dry_run:
            logging.info(
                "[DRY_RUN] Would assign mediaId=%s to layoutId=%s", media_id, layout_id
            )
            return

        r = self._request(
            "GET",
            self._api_url("/layout"),
            params={"layoutId": layout_id, "embed": "regions,playlists", "length": 1},
        )
        if not r.ok:
            raise RuntimeError(
                f"Fetch layout layoutId={layout_id} failed ({r.status_code}): {r.text}"
            )

        layout_data = self._extract_data(r.json())
        if isinstance(layout_data, list):
            layout_data = layout_data[0] if layout_data else {}
        if not isinstance(layout_data, dict):
            raise RuntimeError(f"Unexpected layout fetch response: {r.text}")

        regions = layout_data.get("regions") or []
        if not regions:
            logging.info("Layout layoutId=%s has no regions; creating a full-screen region.", layout_id)
            create_region = self._request(
                "POST",
                self._api_url(f"/region/{layout_id}"),
                data={"type": "frame", "width": 1920, "height": 1080, "top": 0, "left": 0},
            )
            if not create_region.ok:
                raise RuntimeError(
                    f"Create region for layoutId={layout_id} failed "
                    f"({create_region.status_code}): {create_region.text}"
                )

            r = self._request(
                "GET",
                self._api_url("/layout"),
                params={"layoutId": layout_id, "embed": "regions,playlists", "length": 1},
            )
            if not r.ok:
                raise RuntimeError(
                    f"Fetch layout layoutId={layout_id} after region creation failed "
                    f"({r.status_code}): {r.text}"
                )
            layout_data = self._extract_data(r.json())
            if isinstance(layout_data, list):
                layout_data = layout_data[0] if layout_data else {}
            regions = layout_data.get("regions") if isinstance(layout_data, dict) else []
            if not regions:
                raise RuntimeError(
                    f"Layout layoutId={layout_id} still has no regions after creation."
                )

        first_region = regions[0] if isinstance(regions, list) else next(iter(regions.values()))
        playlist_id = None
        if isinstance(first_region, dict):
            region_playlist = first_region.get("regionPlaylist") or {}
            if isinstance(region_playlist, dict):
                playlist_id = str(
                    region_playlist.get("playlistId") or region_playlist.get("id") or ""
                )

        if not playlist_id:
            raise RuntimeError(
                f"Could not determine playlistId for first region of layoutId={layout_id}."
            )

        data = [("media[]", str(media_id))]
        r = self._request(
            "POST",
            self._api_url(f"/playlist/library/assign/{playlist_id}"),
            data=data,
        )
        if not r.ok:
            raise RuntimeError(
                f"Assign media to playlistId={playlist_id} failed ({r.status_code}): {r.text}"
            )

        logging.info(
            "Assigned mediaId=%s to playlistId=%s (layoutId=%s)",
            media_id,
            playlist_id,
            layout_id,
        )

    def deploy_calendar_package_to_layout(
        self,
        layout_name: str,
        package_path: Path,
        tags: List[str],
        publish: bool,
        assign_to_display_group_id: Optional[str],
        immediate_show: bool,
        dry_run: bool,
    ) -> Optional[str]:
        """Orchestrate the full calendar package deploy pipeline.

        Steps:

        1. Upload the ``.htz`` package to the Xibo library.
        2. Find or create the named layout.
        3. Check the layout out for editing when needed.
        4. Assign the uploaded package to the layout's first region playlist.
        5. Publish the layout after package assignment.
        6. Re-resolve the layout ID by name after publish (Xibo may renumber it).
        7. Tag the layout with *tags*.
        8. Publish and re-resolve again because tags can create a draft in Xibo 4.4.
        9. Assign the layout to the display group (when *assign_to_display_group_id* is set).
        10. Change the active layout on the display group (when *immediate_show* is ``True``).

        Args:
            layout_name: Display name of the target layout.
            package_path: Local ``.htz`` file to upload.
            tags: Tags to attach to both the media item and the layout.
            publish: Whether to publish the layout after package assignment.
            assign_to_display_group_id: Optional display group ID to assign
                the layout to after publishing.
            immediate_show: Whether to send a change-layout action so online
                players switch immediately.
            dry_run: Whether to skip all mutating operations.

        Returns:
            The deployed layout ID as a string, or ``None`` in dry-run mode.

        Raises:
            RuntimeError: Raised when any step of the pipeline fails.
        """
        # Step 1: Upload package
        media = self.upload_html_package(package_path, tags, dry_run)
        media_id: Optional[str] = None
        if media is not None:
            media_id = str(
                media.get("mediaId") or media.get("id") or ""
            )
            if not media_id:
                raise RuntimeError(f"Upload of '{package_path.name}' did not return a mediaId.")

        # Step 2: Find or create layout
        layout = self.get_or_create_calendar_layout(layout_name, None, dry_run)
        layout_id: Optional[str] = None
        if layout is not None:
            layout_id = str(layout.get("layoutId") or layout.get("id") or "")
            if not layout_id:
                raise RuntimeError(f"Layout '{layout_name}' did not return a layoutId.")

        if dry_run:
            logging.info("[DRY_RUN] Would deploy package to layout '%s'", layout_name)
            return None

        assert layout_id is not None
        assert media_id is not None

        # Step 3: Check out the layout for editing (best-effort; 422 = already editable)
        checkout_r = self._request("PUT", self._api_url(f"/layout/checkout/{layout_id}"))
        if checkout_r.ok:
            checkout_data = self._extract_data(checkout_r.json())
            new_id = _layout_id_from_payload(checkout_data)
            if new_id and new_id != layout_id:
                logging.info("Checkout returned new draft layoutId=%s (was %s)", new_id, layout_id)
                layout_id = new_id
            elif not new_id:
                logging.warning("Checkout layoutId=%s succeeded but returned no layout ID: %s", layout_id, checkout_r.text)
                draft_id = self.get_draft_layout_id(layout_id)
                if draft_id:
                    layout_id = draft_id
        else:
            already_editable = False
            if checkout_r.status_code == 422:
                try:
                    msg = checkout_r.json().get("message", "").lower()
                    already_editable = "already checked out" in msg
                except Exception:
                    pass
            if already_editable:
                logging.info("LayoutId=%s has a stale checkout; discarding and retrying checkout.", layout_id)
                discard_r = self._request("PUT", self._api_url(f"/layout/discard/{layout_id}"))
                if not discard_r.ok:
                    raise RuntimeError(
                        f"Discard stale checkout for layoutId={layout_id} failed "
                        f"({discard_r.status_code}): {discard_r.text}"
                    )
                checkout_r = self._request("PUT", self._api_url(f"/layout/checkout/{layout_id}"))
                if not checkout_r.ok:
                    raise RuntimeError(
                        f"Checkout layoutId={layout_id} failed after discard "
                        f"({checkout_r.status_code}): {checkout_r.text}"
                    )
                new_id = _layout_id_from_payload(self._extract_data(checkout_r.json()))
                if new_id:
                    logging.info("Retry checkout returned draft layoutId=%s (was %s)", new_id, layout_id)
                    layout_id = new_id
            else:
                draft_id = self.get_draft_layout_id(layout_id)
                if draft_id:
                    logging.info("Using existing draft layoutId=%s for layoutId=%s", draft_id, layout_id)
                    layout_id = draft_id
                else:
                    logging.warning(
                        "Checkout layoutId=%s returned %s; proceeding anyway: %s",
                        layout_id, checkout_r.status_code, checkout_r.text,
                    )

        # Step 4: Assign package to layout's first region playlist
        self.assign_html_package_to_layout(layout_id, media_id, dry_run=False)

        # Step 5: Publish is mandatory because the edit must release its CMS checkout.
        if not publish:
            logging.info("Publishing calendar layoutId=%s despite publish=False; edited layouts must be unlocked.", layout_id)
        self.publish_layout(layout_id, dry_run=False)

        # Step 6: Re-resolve layout ID after publish
        current = self.get_layout_by_name(layout_name)
        if not isinstance(current, dict):
            raise RuntimeError(f"Published calendar layout {layout_name!r} could not be resolved")
        current_id = str(current.get("layoutId") or current.get("id") or "")
        if not current_id:
            raise RuntimeError(f"Published calendar layout {layout_name!r} has no layoutId")
        logging.info("Resolved published calendar layoutId=%s (was %s)", current_id, layout_id)
        layout_id = current_id

        # Step 7: Tagging is part of a successful deployment contract.
        self.tag_layout(layout_id, tags, dry_run=False)

        # Step 8: A tag can itself create a new draft/lock. Finalize it before
        # handing the layout to a display group or immediate-show action.
        self.publish_layout(layout_id, dry_run=False)
        current = self.get_layout_by_name(layout_name)
        if not isinstance(current, dict):
            raise RuntimeError(f"Final-published calendar layout {layout_name!r} could not be resolved")
        current_id = str(current.get("layoutId") or current.get("id") or "")
        if not current_id:
            raise RuntimeError(f"Final-published calendar layout {layout_name!r} has no layoutId")
        logging.info("Resolved final-published calendar layoutId=%s (was %s)", current_id, layout_id)
        layout_id = current_id

        # Step 9: Assign to display group
        if assign_to_display_group_id:
            self.assign_layouts_to_displaygroup(
                assign_to_display_group_id, [layout_id], dry_run=False
            )

        # Step 10: Immediate show
        if immediate_show and assign_to_display_group_id:
            self.change_layout_on_displaygroup(
                assign_to_display_group_id,
                layout_id,
                download_required=1,
                dry_run=False,
            )

        return layout_id

    def change_layout_on_displaygroup(
        self,
        display_group_id: str,
        layout_id: str,
        duration: Optional[int] = None,
        download_required: int = 1,
        change_mode: str = "replace",
        dry_run: bool = False,
    ) -> None:
        """Send a Change Layout action to a display group to make players show a layout immediately.

        Uses POST /displaygroup/{displayGroupId}/action/changeLayout which is delivered via XMR
        to online players.
        """
        logging.info("Sending changeLayout for layoutId=%s to displayGroupId=%s ...", layout_id, display_group_id)
        if dry_run:
            logging.info(
                "[DRY_RUN] Would send changeLayout(layoutId=%s,duration=%s,changeMode=%s) to displayGroupId=%s",
                layout_id,
                duration,
                change_mode,
                display_group_id,
            )
            return

        data = {"layoutId": layout_id, "changeMode": change_mode, "downloadRequired": download_required}
        if duration is not None:
            data["duration"] = duration

        r = self._request("POST", self._api_url(f"/displaygroup/{display_group_id}/action/changeLayout"), data=data)
        if not r.ok:
            raise RuntimeError(f"Change layout action failed ({r.status_code}): {r.text}")
