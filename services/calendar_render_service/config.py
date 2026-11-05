"""Configuration for the host-local calendar render service."""

from __future__ import annotations

from collections.abc import Mapping
from functools import partial
from dataclasses import dataclass
from pathlib import Path

from xibo_sync.config import getenv_bool, getenv_int
from xibo_sync.env_io import load_settings


@dataclass
class Config:
    cms_base_url: str
    cms_verify_tls: bool
    cms_timeout: int
    cms_client_id: str
    cms_client_secret: str
    calendar_dataset_name: str
    calendar_dataset_code: str | None
    calendar_html_views: tuple[str, ...]
    calendar_template_dir: Path
    calendar_timezone: str
    calendar_event_retention_days: int
    output_dir: Path
    managed_tag: str
    managed_folder_id: str | None
    create_layout_per_upload: bool
    cleanup_old_view_uploads: bool
    assign_layout_on_change: bool
    immediate_show_on_change: bool
    display_group_id: str | None
    trigger_collectnow_on_changes: bool
    xibo_upload_field: str
    render_cron: str
    run_on_start: bool
    log_level: str
    dry_run: bool = False


def load_config(*, directory: Path | None = None, values: Mapping[str, str] | None = None) -> Config:
    """Load service-local read-only files with explicit process overrides."""
    if values is None:
        directory = directory if directory is not None else Path(__file__).resolve().parent
        values, _ = load_settings(directory, service=True)
    getenv = values.get
    get_bool = partial(getenv_bool, values=values)
    get_int = partial(getenv_int, values=values)
    base_url = getenv("CMS_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise ValueError("CMS_BASE_URL must be set")
    client_id = getenv("CMS_CLIENT_ID", "").strip()
    client_secret = getenv("CMS_CLIENT_SECRET", "")
    if not client_id or not client_secret:
        raise ValueError("CMS_CLIENT_ID and CMS_CLIENT_SECRET must be set")

    views = tuple(value.strip() for value in getenv(
        "CALENDAR_HTML_VIEWS", "today,this_week,next_2_weeks"
    ).split(",") if value.strip())
    raw_render_cron = getenv("CALENDAR_RENDER_CRON", "0 0 * * *")
    render_cron = raw_render_cron.strip()
    if not render_cron or "\n" in raw_render_cron or "\r" in raw_render_cron or len(render_cron.split()) != 5:
        raise ValueError("CALENDAR_RENDER_CRON must contain exactly five cron fields")

    return Config(
        cms_base_url=base_url,
        cms_verify_tls=get_bool("CMS_VERIFY_TLS", False),
        cms_timeout=get_int("CMS_TIMEOUT_SECONDS", 30),
        cms_client_id=client_id,
        cms_client_secret=client_secret,
        calendar_dataset_name=getenv("CALENDAR_DATASET_NAME", "office_calendar_events").strip(),
        calendar_dataset_code=getenv("CALENDAR_DATASET_CODE", "").strip() or None,
        calendar_html_views=views,
        calendar_template_dir=Path(getenv("CALENDAR_TEMPLATE_DIR", "/app/templates/calendar").strip()),
        calendar_timezone=getenv("CALENDAR_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin",
        calendar_event_retention_days=get_int("CALENDAR_EVENT_RETENTION_DAYS", 30),
        output_dir=Path(getenv("CALENDAR_RENDER_OUTPUT_DIR", "/tmp/calendar-render").strip()),
        managed_tag=getenv("MANAGED_TAG", "xibo-sync").strip(),
        managed_folder_id=getenv("MANAGED_FOLDER_ID", "").strip() or None,
        create_layout_per_upload=get_bool("CREATE_LAYOUT_PER_UPLOAD", False),
        cleanup_old_view_uploads=get_bool("CALENDAR_CLEANUP_OLD_VIEW_UPLOADS", True),
        assign_layout_on_change=get_bool("ASSIGN_LAYOUT_ON_CHANGE", False),
        immediate_show_on_change=get_bool("IMMEDIATE_SHOW_ON_CHANGE", False),
        display_group_id=getenv("DISPLAY_GROUP_ID", "").strip() or None,
        trigger_collectnow_on_changes=get_bool("TRIGGER_COLLECTNOW_ON_CHANGES", True),
        xibo_upload_field=getenv("XIBO_UPLOAD_FIELD", "files").strip(),
        render_cron=render_cron,
        run_on_start=get_bool("CALENDAR_RENDER_RUN_ON_START", True),
        log_level=getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        dry_run=get_bool("DRY_RUN", False),
    )