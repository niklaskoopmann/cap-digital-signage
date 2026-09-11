"""Configuration for the host-local calendar render service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from xibo_sync.config import getenv_bool, getenv_int


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
    assign_layout_on_change: bool
    immediate_show_on_change: bool
    display_group_id: str | None
    trigger_collectnow_on_changes: bool
    xibo_upload_field: str
    schedule_seconds: int
    dry_run: bool = False


def load_config() -> Config:
    """Load the service-only environment configuration."""
    base_url = os.getenv("CMS_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        raise ValueError("CMS_BASE_URL must be set")
    client_id = os.getenv("CMS_CLIENT_ID", "").strip()
    client_secret = os.getenv("CMS_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise ValueError("CMS_CLIENT_ID and CMS_CLIENT_SECRET must be set")

    views = tuple(value.strip() for value in os.getenv(
        "CALENDAR_HTML_VIEWS", "today,this_week,next_2_weeks"
    ).split(",") if value.strip())
    schedule_seconds = getenv_int("CALENDAR_RENDER_SCHEDULE_SECONDS", 86400)
    if schedule_seconds <= 0:
        raise ValueError("CALENDAR_RENDER_SCHEDULE_SECONDS must be greater than zero")

    return Config(
        cms_base_url=base_url,
        cms_verify_tls=getenv_bool("CMS_VERIFY_TLS", False),
        cms_timeout=getenv_int("CMS_TIMEOUT_SECONDS", 30),
        cms_client_id=client_id,
        cms_client_secret=client_secret,
        calendar_dataset_name=os.getenv("CALENDAR_DATASET_NAME", "office_calendar_events").strip(),
        calendar_dataset_code=os.getenv("CALENDAR_DATASET_CODE", "").strip() or None,
        calendar_html_views=views,
        calendar_template_dir=Path(os.getenv("CALENDAR_TEMPLATE_DIR", "/app/templates/calendar").strip()),
        calendar_timezone=os.getenv("CALENDAR_TIMEZONE", "Europe/Berlin").strip() or "Europe/Berlin",
        calendar_event_retention_days=getenv_int("CALENDAR_EVENT_RETENTION_DAYS", 30),
        output_dir=Path(os.getenv("CALENDAR_RENDER_OUTPUT_DIR", "/tmp/calendar-render").strip()),
        managed_tag=os.getenv("MANAGED_TAG", "xibo-sync").strip(),
        managed_folder_id=os.getenv("MANAGED_FOLDER_ID", "").strip() or None,
        create_layout_per_upload=getenv_bool("CREATE_LAYOUT_PER_UPLOAD", False),
        assign_layout_on_change=getenv_bool("ASSIGN_LAYOUT_ON_CHANGE", False),
        immediate_show_on_change=getenv_bool("IMMEDIATE_SHOW_ON_CHANGE", False),
        display_group_id=os.getenv("DISPLAY_GROUP_ID", "").strip() or None,
        trigger_collectnow_on_changes=getenv_bool("TRIGGER_COLLECTNOW_ON_CHANGES", True),
        xibo_upload_field=os.getenv("XIBO_UPLOAD_FIELD", "files").strip(),
        schedule_seconds=schedule_seconds,
    )