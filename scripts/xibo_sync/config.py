"""Configuration loading and typed runtime settings for the sync tool."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import partial
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


@dataclass
class Config:
    """Typed runtime configuration resolved from the environment."""
    cms_base_url: str
    cms_verify_tls: bool
    cms_timeout: int

    auth_mode: str
    cms_client_id: Optional[str]
    cms_client_secret: Optional[str]

    sharepoint_media_dir: Path
    calendar_image_dir: Path
    sync_mode: str
    graph_tenant_id: Optional[str]
    graph_client_id: Optional[str]
    graph_client_secret: Optional[str]
    graph_sharepoint_site_id: Optional[str]
    graph_sharepoint_folder: str
    graph_calendar_user_id: Optional[str]
    graph_calendar_days_ahead: int
    graph_calendar_keep_snapshots: int
    graph_timeout_seconds: int
    graph_base_url: str
    calendar_json_path: Path
    calendar_dataset_name: str
    calendar_dataset_code: Optional[str]
    calendar_upload_cancelled_events: bool
    media_extensions: Tuple[str, ...]
    compare_mode: str

    managed_tag: str
    delete_layout_with_media: bool
    only_delete_managed_tag: bool
    managed_folder_id: Optional[str]

    upload_new_local: bool
    delete_remote_not_local: bool
    dry_run: bool

    display_group_id: Optional[str]
    trigger_collectnow_on_changes: bool

    # Layout/display workflow
    create_layout_per_upload: bool
    assign_layout_on_change: bool
    publish_on_change: bool
    immediate_show_on_change: bool

    xibo_upload_field: str
    hash_tag_prefix: str

    log_level: str
    log_file: Optional[str]

    # Calendar HTML packages
    calendar_enable_html: bool
    calendar_html_views: Tuple[str, ...]
    calendar_template_dir: Path
    calendar_auto_publish: bool
    calendar_timezone: str
    calendar_package_retention_days: int
    calendar_event_retention_days: int


def getenv_bool(name: str, default: bool, *, values: Mapping[str, str] | None = None) -> bool:
    """Read a boolean environment variable with common truthy values.

    Args:
        name: Environment variable name.
        default: Default boolean value when the variable is unset.

    Returns:
        Parsed boolean value.
    """
    v = (os.environ if values is None else values).get(name, str(default)).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def getenv_int(name: str, default: int, *, values: Mapping[str, str] | None = None) -> int:
    """Read an integer environment variable with a fallback default.

    Args:
        name: Environment variable name.
        default: Default integer value when the variable is unset.

    Returns:
        Parsed integer value.
    """
    try:
        return int((os.environ if values is None else values).get(name, str(default)).strip())
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def load_config(sync_mode: str | None = None, *, values: Mapping[str, str] | None = None) -> Config:
    """Load an explicit effective mapping, or the environment for direct callers.

    Returns:
        A populated :class:`Config` instance.

    Raises:
        ValueError: Raised when a required setting is missing or invalid.
    """
    effective = os.environ if values is None else values
    getenv = effective.get
    get_bool = partial(getenv_bool, values=effective)
    get_int = partial(getenv_int, values=effective)
    cms_base_url = getenv("CMS_BASE_URL", "").strip().rstrip("/")
    if not cms_base_url:
        raise ValueError("CMS_BASE_URL must be set (e.g. http://192.168.1.1)")

    auth_mode = getenv("AUTH_MODE", "oauth").strip().lower()
    if auth_mode not in ("none", "oauth"):
        raise ValueError("AUTH_MODE must be 'none' or 'oauth'")

    cms_client_id = getenv("CMS_CLIENT_ID", "").strip() or None
    cms_client_secret = getenv("CMS_CLIENT_SECRET", "") or None
    if auth_mode == "oauth" and (not cms_client_id or not cms_client_secret):
        raise ValueError("For AUTH_MODE=oauth set CMS_CLIENT_ID and CMS_CLIENT_SECRET in secrets.json or the process environment")

    sharepoint_dir = getenv("SHAREPOINT_MEDIA_DIR", "").strip()
    if not sharepoint_dir:
        raise ValueError("SHAREPOINT_MEDIA_DIR must be set")

    configured_sync_mode = (sync_mode or getenv("SYNC_MODE", "full")).strip().lower()
    if configured_sync_mode not in ("full", "download", "upload"):
        raise ValueError("SYNC_MODE must be 'full', 'download', or 'upload'")

    graph_tenant_id = getenv("GRAPH_TENANT_ID", "").strip() or None
    graph_client_id = getenv("GRAPH_CLIENT_ID", "").strip() or None
    graph_client_secret = getenv("GRAPH_CLIENT_SECRET", "") or None
    graph_sharepoint_site_id = getenv("GRAPH_SHAREPOINT_SITE_ID", "").strip() or None
    graph_calendar_user_id = getenv("GRAPH_CALENDAR_USER_ID", "").strip() or None
    required_graph_settings = {
        "GRAPH_TENANT_ID": graph_tenant_id,
        "GRAPH_CLIENT_ID": graph_client_id,
        "GRAPH_CLIENT_SECRET": graph_client_secret,
        "GRAPH_SHAREPOINT_SITE_ID": graph_sharepoint_site_id,
        "GRAPH_CALENDAR_USER_ID": graph_calendar_user_id,
    }
    if configured_sync_mode in ("full", "download"):
        missing_graph_settings = [key for key, value in required_graph_settings.items() if not value]
        if missing_graph_settings:
            raise ValueError(f"Required for SYNC_MODE={configured_sync_mode}: {', '.join(missing_graph_settings)}")

    graph_calendar_days_ahead = get_int("GRAPH_CALENDAR_DAYS_AHEAD", 14)
    graph_calendar_keep_snapshots = get_int("GRAPH_CALENDAR_KEEP_SNAPSHOTS", 10)
    graph_timeout_seconds = get_int("GRAPH_TIMEOUT_SECONDS", 30)
    for key, value in (
        ("GRAPH_CALENDAR_DAYS_AHEAD", graph_calendar_days_ahead),
        ("GRAPH_CALENDAR_KEEP_SNAPSHOTS", graph_calendar_keep_snapshots),
        ("GRAPH_TIMEOUT_SECONDS", graph_timeout_seconds),
    ):
        if value < 1:
            raise ValueError(f"{key} must be >= 1")

    exts = getenv("MEDIA_EXTENSIONS", ".jpg,.jpeg,.png,.gif,.mp4").strip()
    media_extensions = tuple(e.strip().lower() for e in exts.split(",") if e.strip())

    compare_mode = getenv("COMPARE_MODE", "filename").strip().lower()
    if compare_mode not in ("filename", "hash"):
        raise ValueError("COMPARE_MODE must be 'filename' or 'hash'")

    _html_views_raw = getenv("CALENDAR_HTML_VIEWS", "today,this_week,next_2_weeks").strip()
    calendar_html_views: Tuple[str, ...] = tuple(
        v.strip() for v in _html_views_raw.split(",") if v.strip()
    )

    return Config(
        cms_base_url=cms_base_url,
        cms_verify_tls=get_bool("CMS_VERIFY_TLS", False),
        cms_timeout=get_int("CMS_TIMEOUT_SECONDS", 30),

        auth_mode=auth_mode,
        cms_client_id=cms_client_id,
        cms_client_secret=cms_client_secret,

        sharepoint_media_dir=Path(sharepoint_dir),
        calendar_image_dir=Path(getenv("CALENDAR_IMAGE_DIR", "../calendar_images").strip()),
        sync_mode=configured_sync_mode,
        graph_tenant_id=graph_tenant_id,
        graph_client_id=graph_client_id,
        graph_client_secret=graph_client_secret,
        graph_sharepoint_site_id=graph_sharepoint_site_id,
        graph_sharepoint_folder=getenv("GRAPH_SHAREPOINT_FOLDER", "test_images").strip(),
        graph_calendar_user_id=graph_calendar_user_id,
        graph_calendar_days_ahead=graph_calendar_days_ahead,
        graph_calendar_keep_snapshots=graph_calendar_keep_snapshots,
        graph_timeout_seconds=graph_timeout_seconds,
        graph_base_url=getenv("GRAPH_BASE_URL", "https://graph.microsoft.com/v1.0").strip().rstrip("/"),
        calendar_json_path=Path(getenv(
            "CALENDAR_JSON_PATH",
            r"C:\Users\rogrun\OneDrive - Capgemini\Xibo_Sync\office_calendar",
        ).strip()),
        calendar_dataset_name=getenv("CALENDAR_DATASET_NAME", "office_calendar_events").strip(),
        calendar_dataset_code=getenv("CALENDAR_DATASET_CODE", "").strip() or None,
        calendar_upload_cancelled_events=get_bool("CALENDAR_UPLOAD_CANCELLED_EVENTS", False),
        media_extensions=media_extensions,
        compare_mode=compare_mode,

        managed_tag=getenv("MANAGED_TAG", "xibo-sync").strip(),
        delete_layout_with_media=get_bool("DELETE_LAYOUT_WITH_MEDIA", False),
        only_delete_managed_tag=get_bool("ONLY_DELETE_MANAGED_TAG", True),
        managed_folder_id=getenv("MANAGED_FOLDER_ID", "").strip() or None,

        upload_new_local=get_bool("UPLOAD_NEW_LOCAL", True),
        delete_remote_not_local=get_bool("DELETE_REMOTE_NOT_LOCAL", False),
        dry_run=get_bool("DRY_RUN", False),

        display_group_id=getenv("DISPLAY_GROUP_ID", "").strip() or None,
        trigger_collectnow_on_changes=get_bool("TRIGGER_COLLECTNOW_ON_CHANGES", True),

        # Layout/display workflow flags
        create_layout_per_upload=get_bool("CREATE_LAYOUT_PER_UPLOAD", False),
        assign_layout_on_change=get_bool("ASSIGN_LAYOUT_ON_CHANGE", False),
        publish_on_change=get_bool("PUBLISH_ON_CHANGE", True),
        immediate_show_on_change=get_bool("IMMEDIATE_SHOW_ON_CHANGE", False),

        xibo_upload_field=getenv("XIBO_UPLOAD_FIELD", "files").strip(),
        hash_tag_prefix=getenv("HASH_TAG_PREFIX", "sha256:").strip(),

        log_level=getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_file=getenv("LOG_FILE", "").strip() or None,

        calendar_enable_html=get_bool("CALENDAR_ENABLE_HTML", False),
        calendar_html_views=calendar_html_views,
        calendar_template_dir=Path(getenv(
            "CALENDAR_TEMPLATE_DIR",
            "templates/calendar",
        ).strip()),
        calendar_auto_publish=get_bool("CALENDAR_AUTO_PUBLISH", True),
        calendar_timezone=getenv("CALENDAR_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin",
        calendar_package_retention_days=get_int("CALENDAR_PACKAGE_RETENTION_DAYS", 30),
        calendar_event_retention_days=get_int("CALENDAR_EVENT_RETENTION_DAYS", 30),
    )
