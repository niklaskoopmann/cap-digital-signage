"""Fetch, render, and upload calendar images on the Xibo host."""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from zoneinfo import ZoneInfo

from xibo_sync.app import _upload_media_with_optional_layout
from xibo_sync.calendar_data import dataset_rows_to_events, filter_events_by_retention
from xibo_sync.calendar_html import generate_calendar_images
from xibo_sync.client import XiboClient

from .config import Config, load_config


def run_once(cfg: Config, *, client: XiboClient | None = None, now: datetime | None = None) -> dict[str, int]:
    """Run one fetch-render-upload cycle and return its counts."""
    xibo = client or XiboClient(cfg.cms_base_url, cfg.cms_verify_tls, cfg.cms_timeout)
    if client is None:
        xibo.authenticate_oauth(cfg.cms_client_id, cfg.cms_client_secret)

    dataset = xibo.get_dataset(cfg.calendar_dataset_name, cfg.calendar_dataset_code)
    if not dataset:
        logging.warning("Calendar DataSet %r is missing; skipping render cycle", cfg.calendar_dataset_name)
        return {"events": 0, "images": 0, "uploads": 0}
    dataset_id = str(dataset.get("dataSetId") or dataset.get("id") or "")
    if not dataset_id:
        raise RuntimeError(f"Calendar DataSet response did not include an ID: {dataset}")

    events = dataset_rows_to_events(xibo.get_dataset_data(dataset_id))
    events = filter_events_by_retention(
        events, cfg.calendar_event_retention_days, cfg.calendar_timezone, now=now
    )
    if not events:
        logging.warning("Calendar DataSet is empty after retention; skipping render cycle")
        return {"events": 0, "images": 0, "uploads": 0}

    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    images = generate_calendar_images(
        events,
        list(cfg.calendar_html_views),
        cfg.calendar_template_dir,
        cfg.output_dir,
        cfg.calendar_timezone,
        now=now,
        write_images=not cfg.dry_run,
    )
    uploads = 0
    for image in images:
        tags = [cfg.managed_tag, "calendar-html", "calendar-image", f"calendar-{image.view_type}"]
        _upload_media_with_optional_layout(xibo, cfg, file_path=image.image_path, tags=tags)
        uploads += 1
    if uploads and cfg.trigger_collectnow_on_changes and cfg.display_group_id:
        xibo.collect_now(cfg.display_group_id, dry_run=cfg.dry_run)
    logging.info("Calendar cycle complete: events=%d images=%d uploads=%d", len(events), len(images), uploads)
    return {"events": len(events), "images": len(images), "uploads": uploads}


def seconds_until_midnight(now: datetime, timezone: str | tzinfo) -> float:
    """Return seconds from ``now`` until the next local midnight."""
    local_timezone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    local_now = now.astimezone(local_timezone) if now.tzinfo else now.replace(tzinfo=local_timezone)
    tomorrow = (local_now + timedelta(days=1)).date()
    next_midnight = datetime.combine(tomorrow, datetime.min.time(), tzinfo=local_timezone)
    return max(0.0, (next_midnight - local_now).total_seconds())


def run_forever(
    cfg: Config,
    *,
    job=run_once,
    now_fn=datetime.now,
    sleep_fn=time.sleep,
) -> None:
    """Run the job at local midnight, with failures isolated to one cycle."""
    delay = seconds_until_midnight(now_fn(), cfg.calendar_timezone)
    while True:
        sleep_fn(delay)
        try:
            job(cfg, now=now_fn())
        except Exception:
            logging.exception("Calendar render cycle failed; continuing")
        delay = cfg.schedule_seconds


def main() -> None:
    cfg = load_config()
    run_forever(cfg)


if __name__ == "__main__":
    main()