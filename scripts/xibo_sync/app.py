"""Application orchestration for the modular Xibo sync workflow."""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.prompt import Confirm

from .calendar_data import (
    CALENDAR_COLUMNS,
    LEGACY_COLUMN_ALIASES,
    csv_bytes,
    filter_events_by_retention,
    load_events,
)
from .calendar_html import generate_calendar_images
from .client import XiboClient, media_layout_ownership_tag
from .config import load_config
from .env_io import read_env_file, write_env_file
from .media import build_local_index, build_remote_index, list_local_media
from .ui import console, ui_error, ui_header, ui_info, ui_ok, ui_settings_table, ui_warn
from .wizard import config_wizard


def setup_logging(level: str, log_file: str | None, scripts_dir: Path) -> None:
    """Configure console and optional file logging for a sync run.

    Args:
        level: Logging level name such as ``INFO`` or ``DEBUG``.
        log_file: Relative or absolute file path for optional file logging.
        scripts_dir: Base directory used to resolve relative log paths.
    """
    handlers: list[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        lf_path = (scripts_dir / log_file).resolve() if not Path(log_file).is_absolute() else Path(log_file)
        lf_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(lf_path, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


def run_calendar_upload(cfg, scripts_dir: Path, *, now: datetime | None = None) -> int:
    """Replace the configured Xibo DataSet with the newest calendar snapshot."""
    calendar_path = cfg.calendar_json_path
    if not calendar_path.is_absolute():
        calendar_path = (scripts_dir / calendar_path).resolve()

    snapshot, rows = load_events(calendar_path, cfg.calendar_upload_cancelled_events)
    retained_rows = filter_events_by_retention(
        rows,
        cfg.calendar_event_retention_days,
        cfg.calendar_timezone,
        now=now,
    )
    ui_info(f"Calendar retention excluded {len(rows) - len(retained_rows)} event(s)")
    rows = retained_rows
    ui_info(f"Calendar snapshot: {snapshot}")
    ui_info(f"Calendar rows: {len(rows)} | DataSet: {cfg.calendar_dataset_name}")
    ui_info(f"Calendar columns: {len(CALENDAR_COLUMNS)}")

    xibo = XiboClient(cfg.cms_base_url, cfg.cms_verify_tls, cfg.cms_timeout)
    if cfg.auth_mode == "oauth":
        assert cfg.cms_client_id and cfg.cms_client_secret
        xibo.authenticate_oauth(cfg.cms_client_id, cfg.cms_client_secret)
    xibo.health_check()
    ui_ok("Xibo API reachable")

    if cfg.dry_run:
        ui_ok(f"[DRY_RUN] Would replace DataSet with {len(rows)} row(s)")
        return 0

    dataset = xibo.get_dataset(cfg.calendar_dataset_name, cfg.calendar_dataset_code)
    if dataset is None:
        dataset = xibo.create_dataset(cfg.calendar_dataset_name, cfg.calendar_dataset_code)
        ui_ok(f"Created DataSet: {cfg.calendar_dataset_name}")
    else:
        ui_info(f"Using existing DataSet: {cfg.calendar_dataset_name}")

    dataset_id = str(dataset.get("dataSetId") or dataset.get("id") or "")
    if not dataset_id:
        raise RuntimeError(f"DataSet response did not include an ID: {dataset}")

    columns = xibo.list_dataset_columns(dataset_id)
    columns_by_heading = {
        str(column.get("heading")): column
        for column in columns
        if column.get("heading")
    }
    columns_by_heading_lower = {
        str(column.get("heading", "")).lower(): column
        for column in columns
        if column.get("heading")
    }
    column_ids: list[str] = []
    for order, heading in enumerate(CALENDAR_COLUMNS, 1):
        column = columns_by_heading.get(heading)
        if column is None:
            column = next(
                (
                    columns_by_heading_lower[alias.lower()]
                    for alias in LEGACY_COLUMN_ALIASES.get(heading, ())
                    if alias.lower() in columns_by_heading_lower
                ),
                None,
            )
            if column is not None:
                column = xibo.update_dataset_column(dataset_id, column, heading, order)
            else:
                column = xibo.create_dataset_column(dataset_id, heading, order)
        column_id = str(column.get("dataSetColumnId") or column.get("id") or "")
        if not column_id:
            raise RuntimeError(f"DataSet column response did not include an ID: {column}")
        column_ids.append(column_id)

    xibo.import_dataset_csv(dataset_id, csv_bytes(rows), column_ids)
    canonical_headings = {heading.lower() for heading in CALENDAR_COLUMNS}
    used_column_ids = set(column_ids)
    for column in columns:
        heading = str(column.get("heading", ""))
        column_id = str(column.get("dataSetColumnId") or column.get("id") or "")
        if (
            heading.lower() in {alias.lower() for aliases in LEGACY_COLUMN_ALIASES.values() for alias in aliases}
            and heading.lower() not in canonical_headings
            and column_id not in used_column_ids
        ):
            xibo.delete_dataset_column(dataset_id, column)
    ui_ok(f"Replaced DataSet rows: {len(rows)}")

    if cfg.trigger_collectnow_on_changes and cfg.display_group_id:
        xibo.collect_now(cfg.display_group_id, dry_run=False)
        ui_ok("Collect Now triggered")
    return 0


def run_calendar_html_upload(
    cfg,
    scripts_dir: Path,
    *,
    renderer=None,
) -> int:
    """Render calendar HTML views as PNGs and upload them as normal media.

    Args:
        cfg: Populated :class:`~xibo_sync.config.Config` instance.
        scripts_dir: The ``scripts/`` directory used to resolve relative paths
            and as the base for the package output directory.

    Returns:
        ``0`` on success, ``2`` on failure.
    """
    calendar_path = cfg.calendar_json_path
    if not calendar_path.is_absolute():
        calendar_path = (scripts_dir / calendar_path).resolve()

    _snapshot, events = load_events(calendar_path, cfg.calendar_upload_cancelled_events)
    ui_info(f"Calendar snapshot loaded: {len(events)} event(s)")

    # Resolve template directory
    template_dir = cfg.calendar_template_dir
    if not template_dir.is_absolute():
        template_dir = (scripts_dir / template_dir).resolve()

    # Validate that all requested views have corresponding config files
    views_dir = template_dir / "views"
    missing_views = []
    for view_type in cfg.calendar_html_views:
        view_config_path = views_dir / f"{view_type}.json"
        if not view_config_path.exists():
            missing_views.append((view_type, view_config_path))

    if missing_views:
        ui_error(f"Calendar view config file(s) not found:")
        for view_type, path in missing_views:
            ui_error(f"  - {view_type}: {path}")
        return 2

    output_dir = cfg.calendar_image_dir
    if not output_dir.is_absolute():
        output_dir = (scripts_dir / output_dir).resolve()

    images = generate_calendar_images(
        events,
        list(cfg.calendar_html_views),
        template_dir,
        output_dir,
        cfg.calendar_timezone,
        renderer=renderer,
        write_images=not cfg.dry_run,
    )

    if not images:
        ui_warn("No calendar images were generated. Check CALENDAR_HTML_VIEWS and the snapshot.")
        return 0

    for image in images:
        ui_info(f"Calendar image: {image.image_path} ({image.event_count} event(s))")

    if cfg.dry_run:
        for image in images:
            ui_ok(f"[DRY_RUN] Would render and upload: {image.image_path}")
        return 0

    xibo = XiboClient(cfg.cms_base_url, cfg.cms_verify_tls, cfg.cms_timeout)
    if cfg.auth_mode == "oauth":
        assert cfg.cms_client_id and cfg.cms_client_secret
        xibo.authenticate_oauth(cfg.cms_client_id, cfg.cms_client_secret)
    xibo.health_check()
    ui_ok("Xibo API reachable")

    for image in images:
        start_date = image.window_start.strftime("%Y-%m-%d")
        tags = [
            cfg.managed_tag,
            "calendar-html",
            "calendar-image",
            f"calendar-{image.view_type}",
            start_date,
        ]
        _upload_media_with_optional_layout(
            xibo,
            cfg,
            file_path=image.image_path,
            tags=tags,
        )
        ui_ok(f"Uploaded: {image.image_path.name} ({image.event_count} event(s))")

    return 0


def _publish_and_resolve_layout(xibo: XiboClient, layout_id: str, layout_name: str, media_id: str, dry_run: bool) -> str:
    """Publish a layout and return its authoritative post-publish ID."""
    try:
        xibo.publish_layout(layout_id, dry_run=dry_run)
    except Exception as publish_error:
        if dry_run or "failed (404)" not in str(publish_error):
            raise
        candidate_ids = []
        draft_id = xibo.get_draft_layout_id(layout_id)
        if draft_id and draft_id != layout_id:
            candidate_ids.append(draft_id)
        resolved = xibo.get_layout_by_name(layout_name)
        name_id = str(resolved.get("layoutId") or resolved.get("id") or "") if isinstance(resolved, dict) else ""
        if name_id and name_id != layout_id and name_id not in candidate_ids:
            candidate_ids.append(name_id)
        for candidate_id in candidate_ids:
            try:
                xibo.publish_layout(candidate_id, dry_run=False)
            except Exception:
                continue
            layout_id = candidate_id
            break
        else:
            current = xibo.get_layout_by_id(layout_id)
            if not (
                isinstance(current, dict)
                and str(current.get("publishedStatusId")) == "1"
                and not xibo.get_draft_layout_id(layout_id)
            ):
                raise publish_error
    if dry_run:
        return layout_id
    current = xibo.get_layout_by_name(layout_name)
    if not isinstance(current, dict):
        raise RuntimeError(f"Published layout {layout_name!r} could not be resolved for mediaId={media_id}")
    current_id = str(current.get("layoutId") or current.get("id") or "")
    if not current_id:
        raise RuntimeError(f"Published layout {layout_name!r} has no layoutId for mediaId={media_id}")
    return current_id


def _upload_media_with_optional_layout(
    xibo: XiboClient,
    cfg,
    *,
    file_path: Path,
    tags: list[str],
    extra_layout_tags: list[str] | None = None,
):
    """Upload one media file and run its configured full-screen layout lifecycle."""
    created = xibo.upload_media(
        file_path=file_path,
        name=file_path.name,
        folder_id=cfg.managed_folder_id,
        tags=tags,
        preferred_field=cfg.xibo_upload_field,
        dry_run=cfg.dry_run,
    )
    if not cfg.create_layout_per_upload or not created:
        return created
    media_id = str(created.get("mediaId") or created.get("id") or "") if isinstance(created, dict) else ""
    if not media_id and isinstance(created, dict) and created.get("files"):
        media_id = str(created["files"][0].get("mediaId") or "")
    if not media_id:
        return created
    layout = xibo.create_fullscreen_layout(created, dry_run=cfg.dry_run)
    layout_id = str(layout.get("layoutId") or layout.get("id") or "") if isinstance(layout, dict) else ""
    if not layout_id:
        return created
    layout_name = (layout.get("layout") or layout.get("name")) if isinstance(layout, dict) else None
    if not layout_name:
        raise RuntimeError(f"Created layout for mediaId={media_id} has no name for publish recovery")
    layout_id = _publish_and_resolve_layout(xibo, layout_id, layout_name, media_id, cfg.dry_run)
    ownership_tag = media_layout_ownership_tag(media_id)
    layout_tags = [cfg.managed_tag, ownership_tag, *(extra_layout_tags or [])]
    xibo.tag_layout(layout_id, layout_tags, dry_run=cfg.dry_run)
    layout_id = _publish_and_resolve_layout(xibo, layout_id, layout_name, media_id, cfg.dry_run)
    if cfg.assign_layout_on_change and cfg.display_group_id:
        xibo.assign_layouts_to_displaygroup(cfg.display_group_id, [layout_id], dry_run=cfg.dry_run)
    if cfg.immediate_show_on_change and cfg.display_group_id:
        xibo.change_layout_on_displaygroup(
            cfg.display_group_id, layout_id, download_required=1, dry_run=cfg.dry_run
        )
    return created


def run_media_sync(cfg, run_delete: bool, *, xibo: XiboClient | None = None) -> int:
    """Run the media comparison, upload, and cleanup workflow."""
    changes_made = False
    try:
        if xibo is None:
            ui_info("Step 1: Checking Xibo API availability ...")
            xibo = XiboClient(cfg.cms_base_url, cfg.cms_verify_tls, cfg.cms_timeout)
            if cfg.auth_mode == "oauth":
                assert cfg.cms_client_id and cfg.cms_client_secret
                xibo.authenticate_oauth(cfg.cms_client_id, cfg.cms_client_secret)
            xibo.health_check()
            ui_ok("Xibo API reachable")
        ui_info("Step 2: Scanning local media files ...")
        local_files = list_local_media(cfg.sharepoint_media_dir, cfg.media_extensions)
        ui_ok(f"Found {len(local_files)} local media file(s)")
        local_index = build_local_index(local_files, cfg.compare_mode, cfg.hash_tag_prefix)
        ui_info("Step 3: Fetching Xibo CMS library ...")
        remote_items = xibo.list_library(cfg.managed_tag, cfg.managed_folder_id)
        remote_index = build_remote_index(remote_items, cfg.compare_mode, cfg.hash_tag_prefix)
        ui_ok(f"Fetched {len(remote_items)} remote library item(s) (filtered)")
        ui_info("Step 4: Calculating diff ...")
        to_upload = sorted(set(local_index) - set(remote_index))
        to_delete = sorted(set(remote_index) - set(local_index))
        ui_ok(f"To upload: {len(to_upload)} | Remote-only: {len(to_delete)}")
        if cfg.upload_new_local and to_upload:
            ui_info("Step 5: Uploading new media ...")
            for key in to_upload:
                tags = [cfg.managed_tag]
                if cfg.compare_mode == "hash":
                    tags.append(key)
                _upload_media_with_optional_layout(xibo, cfg, file_path=local_index[key], tags=tags)
                changes_made = True
            ui_ok("Uploads complete")
        else:
            ui_info("Step 5: Upload skipped (nothing to upload or UPLOAD_NEW_LOCAL=false)")
        if run_delete and to_delete:
            ui_info("Step 6: Deleting remote-only media ...")
            if cfg.only_delete_managed_tag:
                ui_warn(f"Safety ON: Only deleting items with MANAGED_TAG='{cfg.managed_tag}'")
            for key in to_delete:
                item = remote_index[key]
                media_id = str(item.get("mediaId") or item.get("id") or "")
                if not media_id:
                    logging.warning("Skipping delete (no mediaId): %s", item)
                    continue
                if cfg.only_delete_managed_tag:
                    tags = item.get("tags", "")
                    if isinstance(tags, list):
                        tags = ",".join(tag.get("tag", "") if isinstance(tag, dict) else str(tag) for tag in tags)
                    tags = str(tags)
                    if tags and cfg.managed_tag not in tags:
                        ui_warn(f"Skipping delete mediaId={media_id} (missing managed tag)")
                        continue
                    if not tags:
                        logging.warning("Deleting mediaId=%s without embedded tags because the library query was already restricted by MANAGED_TAG.", media_id)
                if cfg.delete_layout_with_media:
                    ownership_tag = media_layout_ownership_tag(media_id)
                    layouts = xibo.list_layouts_by_ownership_tag(ownership_tag)
                    for layout in layouts:
                        xibo.cleanup_layout_for_media(
                            layout,
                            media_id=media_id,
                            ownership_tag=ownership_tag,
                            display_group_ids=[cfg.display_group_id] if cfg.display_group_id else [],
                            dry_run=cfg.dry_run,
                        )
                        changes_made = True
                xibo.delete_media(media_id, dry_run=cfg.dry_run)
                changes_made = True
            ui_ok("Deletion complete")
        else:
            ui_info("Step 6: Delete skipped (disabled or none to delete)")
        if cfg.trigger_collectnow_on_changes and changes_made and cfg.display_group_id:
            ui_info("Step 7: Triggering player refresh (Collect Now) ...")
            xibo.collect_now(cfg.display_group_id, dry_run=cfg.dry_run)
            ui_ok("Collect Now triggered")
        else:
            ui_info("Step 7: Refresh skipped (no changes, DISPLAY_GROUP_ID empty, or disabled)")
        ui_ok("SUCCESS: Sync completed")
        return 0
    except Exception as exc:
        logging.exception("FAILED: %s", exc)
        ui_error(f"Sync failed: {exc}")
        return 2


def main() -> int:
    """Run the configured download, hotspot-switch, and Xibo upload phases."""
    scripts_dir = Path(__file__).resolve().parent.parent
    env_path = scripts_dir / ".env"
    parser = argparse.ArgumentParser(description="Download SharePoint media and sync it to Xibo CMS", allow_abbrev=False)
    parser.add_argument("--config", action="store_true", help="Run configuration wizard and exit")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive: accept defaults and skip prompts")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without writing, uploading, or deleting")
    parser.add_argument("--mode", choices=("full", "download", "upload"), help="Select download/upload phases")
    parser.add_argument("--upload-calendar-html", action="store_true", help="Render calendar HTML views to 1920x1080 PNGs and upload as media")
    parser.add_argument("--delete", dest="delete", action="store_true", help="Delete remote-only media without prompt")
    parser.add_argument("--no-delete", dest="delete", action="store_false", help="Do not delete remote-only media")
    parser.set_defaults(delete=None)
    args = parser.parse_args()
    ui_header("Xibo Media Sync", "Download from Microsoft Graph, switch to the Xibo hotspot, then upload")
    env_data = read_env_file(env_path)
    if env_data:
        ui_settings_table(env_data)
    else:
        ui_warn("No .env found yet. You should run the configuration wizard now.")
    console.print()
    if args.config:
        config_wizard(env_path)
        return 0
    override = False if args.yes else Confirm.ask(
        "[bold]Do you want to override/edit configuration values now?[/bold]",
        default=(not env_data),
    )
    if override:
        config_wizard(env_path)
        env_data = read_env_file(env_path)
    load_dotenv(env_path)
    cfg = load_config(sync_mode="upload" if args.upload_calendar_html else args.mode)
    if args.dry_run:
        cfg.dry_run = True
    setup_logging(cfg.log_level, cfg.log_file, scripts_dir)
    for attribute in ("sharepoint_media_dir", "calendar_image_dir"):
        path = getattr(cfg, attribute)
        if not path.is_absolute():
            setattr(cfg, attribute, (scripts_dir / path).resolve())
    if args.upload_calendar_html:
        try:
            return run_calendar_html_upload(cfg, scripts_dir)
        except Exception as exc:
            logging.exception("Calendar HTML upload failed: %s", exc)
            ui_error(f"Calendar HTML upload failed: {exc}")
            return 2
    phase_two = cfg.sync_mode in ("full", "upload")
    run_delete = cfg.delete_remote_not_local
    if phase_two:
        console.print()
        ui_header("Run Options", "Choose whether remote-only media should be deleted this run")
        if args.delete is not None:
            run_delete = bool(args.delete)
        elif not args.yes:
            run_delete = Confirm.ask("Delete media from Xibo that is NOT present locally?", default=cfg.delete_remote_not_local)
        env_data = read_env_file(env_path)
        env_data["DELETE_REMOTE_NOT_LOCAL"] = "true" if run_delete else "false"
        write_env_file(env_path, env_data)
        ui_ok("Saved deletion choice to .env (DELETE_REMOTE_NOT_LOCAL)")
    ui_info(f"SharePoint media dir: {cfg.sharepoint_media_dir}")
    ui_info(f"Mode: {cfg.sync_mode} | Compare: {cfg.compare_mode} | Dry run: {cfg.dry_run}")
    if cfg.sync_mode in ("full", "download"):
        from .graph_download import run_download_phase

        ui_header("Phase 1: Download")
        try:
            download_result = run_download_phase(cfg, scripts_dir)
        except Exception as exc:
            ui_error(f"Phase 1 failed: {exc}")
            return 2
        media = download_result.media
        ui_info(
            f"Calendar snapshot: {download_result.calendar_path or 'not written'} | "
            f"Media downloaded: {media.downloaded}, skipped: {media.skipped}, "
            f"deleted: {media.deleted}, ignored: {media.ignored}"
        )
        if download_result.errors:
            for error in download_result.errors:
                ui_error(error)
            if cfg.sync_mode == "download" or args.yes:
                return 2
            if not Confirm.ask("Continue with existing local data?", default=False):
                return 2
        if cfg.sync_mode == "download":
            return 0
    xibo = None
    if cfg.sync_mode == "full":
        while True:
            ui_header("Switch to Xibo Hotspot", "Connect this PC to the Xibo PC hotspot, then confirm to continue")
            if not Confirm.ask("Connected to the Xibo hotspot?", default=False):
                return 2
            try:
                xibo = XiboClient(cfg.cms_base_url, cfg.cms_verify_tls, cfg.cms_timeout)
                if cfg.auth_mode == "oauth":
                    assert cfg.cms_client_id and cfg.cms_client_secret
                    xibo.authenticate_oauth(cfg.cms_client_id, cfg.cms_client_secret)
                xibo.health_check()
                ui_ok("Xibo API reachable")
                break
            except Exception as exc:
                ui_error(f"Xibo API is not reachable yet: {exc}")
    ui_header("Phase 2: Upload to Xibo")
    media_status = run_media_sync(cfg, run_delete, xibo=xibo)
    try:
        calendar_status = run_calendar_upload(cfg, scripts_dir)
    except Exception as exc:
        logging.exception("Calendar upload failed: %s", exc)
        ui_error(f"Calendar upload failed: {exc}")
        calendar_status = 2
    return 0 if media_status == 0 and calendar_status == 0 else 2
