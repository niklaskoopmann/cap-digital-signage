"""Load Microsoft Graph calendar snapshots and prepare Xibo DataSet imports."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime, timedelta, tzinfo
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

CALENDAR_COLUMNS = (
    "eventIdentifier",
    "icalUid",
    "subject",
    "bodyPreview",
    "bodyHtml",
    "startDateTime",
    "startTimeZone",
    "endDateTime",
    "endTimeZone",
    "isAllDay",
    "isCancelled",
    "showAs",
    "type",
    "location",
    "organizer",
    "organizerEmail",
    "webLink",
    "lastModifiedDateTime",
    
)

_SNAPSHOT_NAME = re.compile(
    r"^office_calendar_events_(?P<timestamp>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})\.json$",
    re.IGNORECASE,
)


def find_latest_snapshot(path: Path) -> Path:
    """Resolve a JSON file or the newest timestamped snapshot in a directory."""
    if path.is_file():
        return path
    if not path.is_dir():
        raise FileNotFoundError(f"Calendar JSON path does not exist: {path}")

    candidates: list[tuple[datetime, Path]] = []
    for candidate in path.iterdir():
        if not candidate.is_file():
            continue
        match = _SNAPSHOT_NAME.match(candidate.name)
        if not match:
            continue
        try:
            timestamp = datetime.strptime(match.group("timestamp"), "%Y-%m-%d_%H-%M-%S")
        except ValueError:
            continue
        candidates.append((timestamp, candidate))

    if not candidates:
        raise FileNotFoundError(
            f"No office_calendar_events_YYYY-MM-DD_HH-MM-SS.json snapshots found in: {path}"
        )
    return max(candidates, key=lambda item: item[0])[1]


def _nested(event: dict[str, Any], *keys: str) -> Any:
    value: Any = event
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key, "")
    return "" if value is None else value


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def flatten_event(event: dict[str, Any]) -> dict[str, str]:
    """Flatten one Microsoft Graph event into the stable DataSet schema."""
    event_id = _text(event.get("id"))
    if not event_id:
        raise ValueError("Calendar event is missing required id")

    organizer_name = _nested(event, "organizer", "emailAddress", "name")
    organizer_email = _nested(event, "organizer", "emailAddress", "address")
    return {
        "eventIdentifier": event_id,
        "icalUid": _text(event.get("iCalUId", event.get("icalUid", ""))),
        "subject": _text(event.get("subject")),
        "bodyPreview": _text(event.get("bodyPreview")),
        "bodyHtml": _text(_nested(event, "body", "content")),
        "startDateTime": _text(_nested(event, "start", "dateTime")),
        "startTimeZone": _text(_nested(event, "start", "timeZone")),
        "endDateTime": _text(_nested(event, "end", "dateTime")),
        "endTimeZone": _text(_nested(event, "end", "timeZone")),
        "isAllDay": _text(event.get("isAllDay")),
        "isCancelled": _text(event.get("isCancelled")),
        "showAs": _text(event.get("showAs")),
        "type": _text(event.get("type")),
        "location": _text(_nested(event, "location", "displayName")),
        "organizer": _text(organizer_name),
        "organizerEmail": _text(organizer_email),
        "webLink": _text(event.get("webLink")),
        "lastModifiedDateTime": _text(event.get("lastModifiedDateTime")),
    }


def dataset_row_to_event(row: dict[str, Any]) -> dict[str, str]:
    """Convert a calendar DataSet row into the flat event shape used by the renderer."""
    return {
        "id": _text(row.get("eventIdentifier")),
        "iCalUId": _text(row.get("icalUid")),
        "subject": _text(row.get("subject")),
        "bodyPreview": _text(row.get("bodyPreview")),
        "bodyHtml": _text(row.get("bodyHtml")),
        "startDateTime": _text(row.get("startDateTime")),
        "startTimeZone": _text(row.get("startTimeZone")),
        "endDateTime": _text(row.get("endDateTime")),
        "endTimeZone": _text(row.get("endTimeZone")),
        "isAllDay": _text(row.get("isAllDay")),
        "isCancelled": _text(row.get("isCancelled")),
        "showAs": _text(row.get("showAs")),
        "type": _text(row.get("type")),
        "location": _text(row.get("location")),
        "organizer": _text(row.get("organizer")),
        "organizerEmail": _text(row.get("organizerEmail")),
        "webLink": _text(row.get("webLink")),
        "lastModifiedDateTime": _text(row.get("lastModifiedDateTime")),
    }


def dataset_rows_to_events(rows: Iterable[dict[str, Any]]) -> list[dict[str, str]]:
    """Convert DataSet rows keyed by :data:`CALENDAR_COLUMNS` into events."""
    return [dataset_row_to_event(row) for row in rows]


def _parse_event_datetime(value: str, timezone_name: str, default_timezone: tzinfo) -> datetime | None:
    if not value:
        return None
    cleaned = re.sub(r"\.(\d{6})\d+", r".\1", value.strip().replace("Z", "+00:00"))
    parsed = datetime.fromisoformat(cleaned)
    if parsed.tzinfo is not None:
        return parsed
    try:
        event_timezone = ZoneInfo(timezone_name) if timezone_name else default_timezone
    except Exception:
        event_timezone = default_timezone
    return parsed.replace(tzinfo=event_timezone)


def filter_events_by_retention(
    events: Iterable[dict[str, Any]],
    retention_days: int,
    timezone: str | tzinfo,
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Drop events whose end is older than the configured retention window."""
    reference_timezone = ZoneInfo(timezone) if isinstance(timezone, str) else timezone
    reference_now = now or datetime.now(reference_timezone)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=reference_timezone)
    else:
        reference_now = reference_now.astimezone(reference_timezone)
    cutoff = reference_now - timedelta(days=retention_days)

    retained: list[dict[str, Any]] = []
    for event in events:
        if "end" in event and isinstance(event.get("end"), dict):
            end_value = _text(event["end"].get("dateTime"))
            end_timezone = _text(event["end"].get("timeZone"))
        else:
            end_value = _text(event.get("endDateTime"))
            end_timezone = _text(event.get("endTimeZone"))
        end = _parse_event_datetime(end_value, end_timezone, reference_timezone)
        if end is None or end.astimezone(reference_timezone) >= cutoff:
            retained.append(event)
    return retained


def load_events(path: Path, include_cancelled: bool = False) -> tuple[Path, list[dict[str, str]]]:
    """Load, filter, and flatten events from the latest configured snapshot."""
    snapshot = find_latest_snapshot(path)
    with snapshot.open("r", encoding="utf-8-sig") as handle:
        document = json.load(handle)

    if not isinstance(document, dict) or not isinstance(document.get("value"), list):
        raise ValueError(f"Calendar JSON must contain a top-level 'value' array: {snapshot}")

    rows = []
    for event in document["value"]:
        if not isinstance(event, dict):
            raise ValueError(f"Calendar value entries must be objects: {snapshot}")
        if not include_cancelled and event.get("isCancelled") is True:
            continue
        rows.append(flatten_event(event))
    return snapshot, rows


def csv_bytes(rows: Iterable[dict[str, str]]) -> bytes:
    """Serialize rows with a header in the column order expected by Xibo."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=CALENDAR_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode("utf-8")
