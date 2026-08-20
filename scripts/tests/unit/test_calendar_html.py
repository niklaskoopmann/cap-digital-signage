"""Unit tests for scripts/xibo_sync/calendar_html.py."""

from __future__ import annotations

import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xibo_sync.calendar_html import (
    CalendarEvent,
    _coerce_timezone,
    filter_events_by_view,
    generate_calendar_packages,
    package_html_to_zip,
    render_calendar_html,
)


def test_coerce_timezone_resolves_named_timezone() -> None:
    resolved_timezone = _coerce_timezone("UTC")

    assert resolved_timezone.utcoffset(datetime.min) == timezone.utc.utcoffset(datetime.min)


def _event(
    event_id: str,
    start: str,
    end: str,
    *,
    subject: str,
    is_all_day: bool = False,
    location: str = "",
    organizer: str = "",
    time_zone: str = "UTC",
) -> dict:
    event = {
        "id": event_id,
        "subject": subject,
        "isAllDay": is_all_day,
        "start": {"dateTime": start, "timeZone": time_zone},
        "end": {"dateTime": end, "timeZone": time_zone},
    }
    if location:
        event["location"] = {"displayName": location}
    if organizer:
        event["organizer"] = {"emailAddress": {"name": organizer}}
    return event


def test_filter_events_by_view_excludes_past_and_sorts_by_start() -> None:
    now = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    events = [
        _event("past", "2026-08-20T07:30:00.0000000", "2026-08-20T08:00:00.0000000", subject="Past"),
        _event("all-day", "2026-08-20T00:00:00.0000000", "2026-08-21T00:00:00.0000000", subject="All day", is_all_day=True),
        _event("later", "2026-08-20T10:00:00.0000000", "2026-08-20T11:30:00.0000000", subject="Later", location="Room A", organizer="Alex"),
        _event("week", "2026-08-22T14:00:00.0000000", "2026-08-22T15:00:00.0000000", subject="Week"),
        _event("future", "2026-09-05T09:00:00.0000000", "2026-09-05T10:00:00.0000000", subject="Future"),
    ]

    today = filter_events_by_view(events, "today", timezone.utc, now=now)
    this_week = filter_events_by_view(events, "this_week", timezone.utc, now=now)
    next_two_weeks = filter_events_by_view(events, "next_2_weeks", timezone.utc, now=now)

    assert [event.subject for event in today] == ["All day", "Later"]
    assert [event.subject for event in this_week] == ["All day", "Later", "Week"]
    assert [event.subject for event in next_two_weeks] == ["All day", "Later", "Week"]
    assert today[1].location == "Room A"
    assert today[1].organizer == "Alex"
    assert today[0].all_day is True


def test_filter_events_by_view_accepts_flattened_datetime_fields() -> None:
    event = {
        "id": "flat-event",
        "subject": "Flattened event",
        "startDateTime": "2026-08-20T10:00:00",
        "startTimeZone": "UTC",
        "endDateTime": "2026-08-20T11:00:00",
        "endTimeZone": "UTC",
    }

    filtered = filter_events_by_view(
        [event],
        "today",
        timezone.utc,
        now=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )

    assert [item.subject for item in filtered] == ["Flattened event"]


def test_filter_events_by_view_rejects_unknown_view() -> None:
    with pytest.raises(ValueError, match="Unknown calendar view"):
        filter_events_by_view([], "monthly", timezone.utc)


def test_render_calendar_html_includes_details_and_empty_state() -> None:
    event = CalendarEvent(
        subject="Planning",
        start=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
        end=datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc),
        location="Room A",
        organizer="Alex",
    )

    html = render_calendar_html([event], "today", timezone.utc, generated_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))
    assert "Calendar - Today" in html
    assert "Planning" in html
    assert "Room A" in html
    assert "Alex" in html
    assert "09:00 (1h 30m)" in html
    assert "1 event(s)" in html

    empty_html = render_calendar_html([], "today", timezone.utc, generated_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc))
    assert "No events scheduled" in empty_html


def test_package_html_to_zip_writes_index_html(tmp_path: Path) -> None:
    package_path = package_html_to_zip("<html>ok</html>", tmp_path, "calendar_today_2026-08-20")

    assert package_path == tmp_path / "calendar_today_2026-08-20.htz"
    with zipfile.ZipFile(package_path) as archive:
        assert archive.namelist() == ["index.html"]
        assert archive.read("index.html") == b"<html>ok</html>"


def test_generate_calendar_packages_creates_expected_packages(tmp_path: Path) -> None:
    now = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    events = [
        _event("today", "2026-08-20T10:00:00.0000000", "2026-08-20T11:00:00.0000000", subject="Today"),
        _event("week", "2026-08-22T10:00:00.0000000", "2026-08-22T11:00:00.0000000", subject="Week"),
    ]

    packages = generate_calendar_packages(events, ("today", "this_week", "next_2_weeks"), tmp_path, timezone.utc, now=now)

    assert [package.view_type for package in packages] == ["today", "this_week", "next_2_weeks"]
    assert [package.event_count for package in packages] == [1, 2, 2]
    assert [package.package_path.name for package in packages] == [
        "calendar_today_2026-08-20.htz",
        "calendar_this_week_2026-08-20.htz",
        "calendar_next_2_weeks_2026-08-20.htz",
    ]
    assert all(package.package_path.exists() for package in packages)

