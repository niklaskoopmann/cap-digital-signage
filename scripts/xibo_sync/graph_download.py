"""Download calendar snapshots and SharePoint media for the offline Xibo phase."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .calendar_data import list_snapshots
from .graph_client import GraphClient

logger = logging.getLogger(__name__)
_MANIFEST_NAME = ".sharepoint_manifest.json"


@dataclass
class MediaDownloadResult:
    downloaded: int = 0
    skipped: int = 0
    deleted: int = 0
    ignored: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class DownloadPhaseResult:
    calendar_path: Path | None = None
    media: MediaDownloadResult = field(default_factory=MediaDownloadResult)
    errors: list[str] = field(default_factory=list)


def calendar_window(
    now: datetime,
    retention_days: int,
    days_ahead: int,
    timezone_name: str,
) -> tuple[datetime, datetime]:
    """Return the calendar query window in the configured timezone."""
    timezone = ZoneInfo(timezone_name)
    reference = now.replace(tzinfo=timezone) if now.tzinfo is None else now.astimezone(timezone)
    return reference - timedelta(days=retention_days), reference + timedelta(days=days_ahead)


def _calendar_path(cfg, scripts_dir: Path) -> Path:
    path = cfg.calendar_json_path
    return path if path.is_absolute() else (scripts_dir / path).resolve()


def download_calendar(
    graph: GraphClient,
    cfg,
    scripts_dir: Path,
    now: datetime,
    dry_run: bool,
) -> Path | None:
    """Fetch all calendar-view pages and atomically save/prune the snapshot."""
    directory = _calendar_path(cfg, scripts_dir)
    if directory.exists() and not directory.is_dir():
        raise RuntimeError(f"CALENDAR_JSON_PATH must be a directory for download: {directory}")
    start, end = calendar_window(
        now,
        cfg.calendar_event_retention_days,
        cfg.graph_calendar_days_ahead,
        cfg.calendar_timezone,
    )
    user_id = quote(cfg.graph_calendar_user_id, safe="@")
    context, events = graph.get_paged(
        f"/users/{user_id}/calendarView",
        params={"startDateTime": start.isoformat(), "endDateTime": end.isoformat()},
    )
    timestamp = now.astimezone(ZoneInfo(cfg.calendar_timezone)) if now.tzinfo else now.replace(tzinfo=ZoneInfo(cfg.calendar_timezone))
    filename = timestamp.strftime("office_calendar_events_%Y-%m-%d_%H-%M-%S.json")
    target = directory / filename
    logger.info("Calendar download returned %d event(s); target: %s", len(events), target)
    if dry_run:
        return None

    directory.mkdir(parents=True, exist_ok=True)
    document = {"value": events}
    if context is not None:
        document = {"@odata.context": context, **document}
    temp_path = target.with_suffix(target.suffix + ".part")
    try:
        temp_path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, target)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    snapshots = sorted(list_snapshots(directory), key=lambda item: item[0], reverse=True)
    for _snapshot_time, old_path in snapshots[cfg.graph_calendar_keep_snapshots:]:
        old_path.unlink()
    return target


def _safe_media_path(directory: Path, name: str) -> Path | None:
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        return None
    root = directory.resolve()
    candidate = (directory / name).resolve()
    if not candidate.is_relative_to(root):
        return None
    return candidate


def _load_manifest(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Could not read SharePoint manifest: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("SharePoint manifest must contain a JSON object")
    return {
        str(name): entry
        for name, entry in data.items()
        if str(name) != _MANIFEST_NAME and isinstance(entry, dict)
    }


def _save_manifest(path: Path, manifest: dict[str, dict]) -> None:
    temp_path = path.with_suffix(path.suffix + ".part")
    try:
        temp_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_path, path)
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise


def download_media(graph: GraphClient, cfg, dry_run: bool) -> MediaDownloadResult:
    """Reconcile the SharePoint folder into local media using its ownership manifest."""
    result = MediaDownloadResult()
    directory = cfg.sharepoint_media_dir
    if not directory.is_absolute():
        directory = directory.resolve()
    manifest_path = directory / _MANIFEST_NAME
    manifest = _load_manifest(manifest_path)
    folder = quote(cfg.graph_sharepoint_folder, safe="/")
    site_id = cfg.graph_sharepoint_site_id
    _context, items = graph.get_paged(f"/sites/{site_id}/drive/root:/{folder}:/children")
    listed_ids = {str(item.get("id")) for item in items if item.get("id")}

    if not dry_run:
        directory.mkdir(parents=True, exist_ok=True)

    for item in items:
        name = str(item.get("name") or "")
        if name == _MANIFEST_NAME:
            result.ignored += 1
            continue
        file_facet = item.get("file")
        if not isinstance(file_facet, dict):
            continue
        if Path(name).suffix.lower() not in cfg.media_extensions:
            continue

        target = _safe_media_path(directory, name)
        if target is None:
            result.errors.append(f"Unsafe SharePoint filename: {name!r}")
            continue

        previous = manifest.get(name)
        if target.exists() and previous is None:
            result.ignored += 1
            logger.warning("Skipping SharePoint item %s because an untracked local file already exists", name)
            continue

        entry = {
            "id": item.get("id"),
            "eTag": item.get("eTag"),
            "size": item.get("size"),
            "lastModifiedDateTime": item.get("lastModifiedDateTime"),
        }
        unchanged = (
            previous is not None
            and all(previous.get(key) == entry[key] for key in ("eTag", "size", "lastModifiedDateTime"))
            and target.is_file()
            and target.stat().st_size == entry["size"]
        )
        if unchanged:
            manifest[name] = entry
            result.skipped += 1
            continue

        if dry_run:
            result.downloaded += 1
            continue
        try:
            graph.download(item, site_id, target)
        except Exception as exc:
            result.errors.append(str(exc))
            continue
        manifest[name] = entry
        result.downloaded += 1
        _save_manifest(manifest_path, manifest)

    stale_names = [
        name for name, entry in manifest.items()
        if entry.get("id") and str(entry["id"]) not in listed_ids
    ]
    for name in stale_names:
        target = _safe_media_path(directory, name)
        if target is None:
            result.errors.append(f"Unsafe filename in SharePoint manifest: {name!r}")
            continue
        if dry_run:
            result.deleted += 1
            del manifest[name]
            continue
        try:
            target.unlink(missing_ok=True)
        except OSError as exc:
            result.errors.append(f"Could not delete stale SharePoint file {name!r}: {exc}")
            continue
        del manifest[name]
        result.deleted += 1

    if not dry_run:
        _save_manifest(manifest_path, manifest)
    return result


def run_download_phase(cfg, scripts_dir: Path, *, graph=None, now: datetime | None = None) -> DownloadPhaseResult:
    """Authenticate once, then independently attempt calendar and media downloads."""
    result = DownloadPhaseResult()
    if graph is None:
        assert cfg.graph_tenant_id and cfg.graph_client_id and cfg.graph_client_secret
        graph = GraphClient(
            cfg.graph_tenant_id,
            cfg.graph_client_id,
            cfg.graph_client_secret,
            cfg.graph_base_url,
            cfg.graph_timeout_seconds,
        )
    try:
        graph.authenticate()
    except Exception as exc:
        result.errors.append(str(exc))
        return result

    reference_now = now or datetime.now(ZoneInfo(cfg.calendar_timezone))
    try:
        result.calendar_path = download_calendar(graph, cfg, scripts_dir, reference_now, cfg.dry_run)
    except Exception as exc:
        result.errors.append(f"Calendar download failed: {exc}")

    try:
        result.media = download_media(graph, cfg, cfg.dry_run)
        result.errors.extend(result.media.errors)
    except Exception as exc:
        result.errors.append(f"SharePoint media download failed: {exc}")
    return result
