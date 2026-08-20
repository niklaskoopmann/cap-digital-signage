"""Load Microsoft Graph calendar snapshots and prepare Xibo DataSet imports."""

from __future__ import annotations

import csv
import io
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

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
    "c11",
    "c12",
    "c13",
    "c14",
    "c15",
    "c16",
    "c17",
    "c18",
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
        "c11": _text(event.get("isCancelled")),
        "c12": _text(event.get("showAs")),
        "c13": _text(event.get("type")),
        "c14": _text(_nested(event, "location", "displayName")),
        "c15": _text(organizer_name),
        "c16": _text(organizer_email),
        "c17": _text(event.get("webLink")),
        "c18": _text(event.get("lastModifiedDateTime")),
    }


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
