"""Fetch, render, and upload calendar images on the Xibo host."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from xibo_sync.app import _upload_media_with_optional_layout
from xibo_sync.calendar_data import dataset_rows_to_events, filter_events_by_retention
from xibo_sync.calendar_html import generate_calendar_images
from xibo_sync.client import XiboClient, media_layout_ownership_tag

from .config import Config, load_config


def setup_logging(level: str) -> None:
    """Configure console logging so cycle progress and warnings reach the container logs."""
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
    )


def cleanup_previous_view_uploads(xibo: XiboClient, cfg: Config, view_type: str, current_media_id: str, now: datetime | None = None) -> None:
    """Remove the previous cycle's media/layout for a given calendar view after the new one has been uploaded."""
    if not cfg.cleanup_old_view_uploads:
        return

    required_tags = [cfg.managed_tag, f"calendar-{view_type}"]
    for old_media in xibo.list_library_by_tags(required_tags, cfg.managed_folder_id):
        old_media_id = str(old_media.get("mediaId") or old_media.get("id") or "")
        if not old_media_id or old_media_id == current_media_id:
            continue

        try:
            if cfg.create_layout_per_upload:
                layouts = xibo.list_layouts_by_ownership_tag(media_layout_ownership_tag(old_media_id))
                new_layouts = xibo.list_layouts_by_ownership_tag(media_layout_ownership_tag(current_media_id))
                new_campaign_id = None
                if new_layouts:
                    new_campaign_id = str(new_layouts[0].get("campaignId") or new_layouts[0].get("layoutCampaignId") or "")
                for layout in layouts:
                    old_campaign_id = layout.get("campaignId") or layout.get("layoutCampaignId")
                    if old_campaign_id and new_campaign_id:
                        xibo.clone_schedule_events_to_campaign(str(old_campaign_id), str(new_campaign_id), dry_run=cfg.dry_run)
                    xibo.cleanup_layout_for_media(
                        layout,
                        media_id=old_media_id,
                        ownership_tag=media_layout_ownership_tag(old_media_id),
                        display_group_ids=[cfg.display_group_id] if cfg.display_group_id else [],
                        dry_run=cfg.dry_run,
                    )
                xibo.delete_media(old_media_id, dry_run=cfg.dry_run)
            else:
                xibo.delete_media(old_media_id, dry_run=cfg.dry_run)
        except Exception:
            logging.warning("Cleanup of old calendar item mediaId=%s for view=%s failed; will retry next cycle", old_media_id, view_type, exc_info=True)


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
        created = _upload_media_with_optional_layout(
            xibo,
            cfg,
            file_path=image.image_path,
            tags=tags,
            extra_layout_tags=[f"calendar-{image.view_type}"],
        )
        if created and isinstance(created, dict):
            current_media_id = str(created.get("mediaId") or created.get("id") or "")
            if current_media_id:
                cleanup_previous_view_uploads(xibo, cfg, image.view_type, current_media_id, now=now)
        uploads += 1
    if uploads and cfg.trigger_collectnow_on_changes and cfg.display_group_id:
        xibo.collect_now(cfg.display_group_id, dry_run=cfg.dry_run)
    logging.info("Calendar cycle complete: events=%d images=%d uploads=%d", len(events), len(images), uploads)
    return {"events": len(events), "images": len(images), "uploads": uploads}


def main() -> int:
    """Run exactly one render cycle and return a process status code."""
    cfg = load_config()
    setup_logging(cfg.log_level)
    try:
        run_once(cfg)
    except Exception:
        logging.exception("Calendar render cycle failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())