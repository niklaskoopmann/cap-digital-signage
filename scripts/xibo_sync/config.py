"""Configuration loading and typed runtime settings for the sync tool."""

from __future__ import annotations

import os
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

    local_media_dir: Path
    calendar_json_path: Path
    calendar_dataset_name: str
    calendar_dataset_code: Optional[str]
    calendar_upload_cancelled_events: bool
    media_extensions: Tuple[str, ...]
    compare_mode: str

    managed_tag: str
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
    calendar_layout_names: Tuple[str, ...]
    calendar_auto_publish: bool
    calendar_timezone: str
    calendar_package_retention_days: int


def getenv_bool(name: str, default: bool) -> bool:
    """Read a boolean environment variable with common truthy values.

    Args:
        name: Environment variable name.
        default: Default boolean value when the variable is unset.

    Returns:
        Parsed boolean value.
    """
    v = os.getenv(name, str(default)).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def getenv_int(name: str, default: int) -> int:
    """Read an integer environment variable with a fallback default.

    Args:
        name: Environment variable name.
        default: Default integer value when the variable is unset.

    Returns:
        Parsed integer value.
    """
    return int(os.getenv(name, str(default)).strip())


def load_config() -> Config:
    """Load configuration from environment variables.

    This expects ``load_dotenv`` to have already populated the process environment.

    Returns:
        A populated :class:`Config` instance.

    Raises:
        ValueError: Raised when a required setting is missing or invalid.
    """
    cms_base_url = os.getenv("CMS_BASE_URL", "").strip().rstrip("/")
    if not cms_base_url:
        raise ValueError("CMS_BASE_URL must be set (e.g. http://192.168.1.1)")

    auth_mode = os.getenv("AUTH_MODE", "oauth").strip().lower()
    if auth_mode not in ("none", "oauth"):
        raise ValueError("AUTH_MODE must be 'none' or 'oauth'")

    cms_client_id = os.getenv("CMS_CLIENT_ID", "").strip() or None
    cms_client_secret = os.getenv("CMS_CLIENT_SECRET", "").strip() or None
    if auth_mode == "oauth" and (not cms_client_id or not cms_client_secret):
        raise ValueError("For AUTH_MODE=oauth you must set CMS_CLIENT_ID and CMS_CLIENT_SECRET in .env")

    local_dir = os.getenv("LOCAL_MEDIA_DIR", "").strip()
    if not local_dir:
        raise ValueError("LOCAL_MEDIA_DIR must be set")

    exts = os.getenv("MEDIA_EXTENSIONS", ".jpg,.jpeg,.png,.gif,.mp4").strip()
    media_extensions = tuple(e.strip().lower() for e in exts.split(",") if e.strip())

    compare_mode = os.getenv("COMPARE_MODE", "filename").strip().lower()
    if compare_mode not in ("filename", "hash"):
        raise ValueError("COMPARE_MODE must be 'filename' or 'hash'")

    _valid_html_views = {"today", "this_week", "next_2_weeks"}
    _html_views_raw = os.getenv("CALENDAR_HTML_VIEWS", "today,this_week,next_2_weeks").strip()
    calendar_html_views: Tuple[str, ...] = tuple(
        v.strip() for v in _html_views_raw.split(",") if v.strip()
    )
    for _v in calendar_html_views:
        if _v not in _valid_html_views:
            raise ValueError(
                f"CALENDAR_HTML_VIEWS contains unknown view '{_v}'. "
                f"Valid values: {', '.join(sorted(_valid_html_views))}"
            )

    _layout_names_raw = os.getenv(
        "CALENDAR_LAYOUT_NAMES",
        "Calendar - Today,Calendar - This Week,Calendar - Next 2 Weeks",
    ).strip()
    calendar_layout_names: Tuple[str, ...] = tuple(
        n.strip() for n in _layout_names_raw.split(",") if n.strip()
    )

    if len(calendar_html_views) != len(calendar_layout_names):
        raise ValueError(
            f"CALENDAR_HTML_VIEWS has {len(calendar_html_views)} entries but "
            f"CALENDAR_LAYOUT_NAMES has {len(calendar_layout_names)}. "
            "They must have the same length."
        )

    return Config(
        cms_base_url=cms_base_url,
        cms_verify_tls=getenv_bool("CMS_VERIFY_TLS", False),
        cms_timeout=getenv_int("CMS_TIMEOUT_SECONDS", 30),

        auth_mode=auth_mode,
        cms_client_id=cms_client_id,
        cms_client_secret=cms_client_secret,

        local_media_dir=Path(local_dir),
        calendar_json_path=Path(os.getenv(
            "CALENDAR_JSON_PATH",
            r"C:\Users\rogrun\OneDrive - Capgemini\Xibo_Sync\office_calendar",
        ).strip()),
        calendar_dataset_name=os.getenv("CALENDAR_DATASET_NAME", "office_calendar_events").strip(),
        calendar_dataset_code=os.getenv("CALENDAR_DATASET_CODE", "").strip() or None,
        calendar_upload_cancelled_events=getenv_bool("CALENDAR_UPLOAD_CANCELLED_EVENTS", False),
        media_extensions=media_extensions,
        compare_mode=compare_mode,

        managed_tag=os.getenv("MANAGED_TAG", "xibo-sync").strip(),
        only_delete_managed_tag=getenv_bool("ONLY_DELETE_MANAGED_TAG", True),
        managed_folder_id=os.getenv("MANAGED_FOLDER_ID", "").strip() or None,

        upload_new_local=getenv_bool("UPLOAD_NEW_LOCAL", True),
        delete_remote_not_local=getenv_bool("DELETE_REMOTE_NOT_LOCAL", False),
        dry_run=getenv_bool("DRY_RUN", False),

        display_group_id=os.getenv("DISPLAY_GROUP_ID", "").strip() or None,
        trigger_collectnow_on_changes=getenv_bool("TRIGGER_COLLECTNOW_ON_CHANGES", True),

        # Layout/display workflow flags
        create_layout_per_upload=getenv_bool("CREATE_LAYOUT_PER_UPLOAD", False),
        assign_layout_on_change=getenv_bool("ASSIGN_LAYOUT_ON_CHANGE", False),
        publish_on_change=getenv_bool("PUBLISH_ON_CHANGE", True),
        immediate_show_on_change=getenv_bool("IMMEDIATE_SHOW_ON_CHANGE", False),

        xibo_upload_field=os.getenv("XIBO_UPLOAD_FIELD", "files").strip(),
        hash_tag_prefix=os.getenv("HASH_TAG_PREFIX", "sha256:").strip(),

        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_file=os.getenv("LOG_FILE", "").strip() or None,

        calendar_enable_html=getenv_bool("CALENDAR_ENABLE_HTML", False),
        calendar_html_views=calendar_html_views,
        calendar_layout_names=calendar_layout_names,
        calendar_auto_publish=getenv_bool("CALENDAR_AUTO_PUBLISH", True),
        calendar_timezone=os.getenv("CALENDAR_TIMEZONE", "UTC").strip() or "UTC",
        calendar_package_retention_days=getenv_int("CALENDAR_PACKAGE_RETENTION_DAYS", 30),
    )
