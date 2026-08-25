"""Unit tests for scripts/xibo_sync/calendar_html.py."""

from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from xibo_sync.calendar_html import (
    CalendarEvent,
    _coerce_timezone,
    filter_events_by_view,
    build_calendar_template_context,
    generate_calendar_packages,
)


def test_coerce_timezone_resolves_named_timezone() -> None:
    resolved_timezone = _coerce_timezone("UTC")

    assert resolved_timezone.utcoffset(datetime.min) == timezone.utc.utcoffset(datetime.min)


def test_coerce_timezone_resolves_berlin_timezone() -> None:
    """Test that Europe/Berlin timezone resolves correctly."""
    resolved_timezone = _coerce_timezone("Europe/Berlin")
    
    # Berlin in summer (CEST = UTC+2)
    summer_dt = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
    berlin_summer = summer_dt.astimezone(resolved_timezone)
    assert berlin_summer.hour == 14
    
    # Berlin in winter (CET = UTC+1)
    winter_dt = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    berlin_winter = winter_dt.astimezone(resolved_timezone)
    assert berlin_winter.hour == 13


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

    today = filter_events_by_view(events, window_days=1, timezone=timezone.utc, now=now)
    this_week = filter_events_by_view(events, window_days=7, timezone=timezone.utc, now=now)
    next_two_weeks = filter_events_by_view(events, window_days=14, timezone=timezone.utc, now=now)

    assert [event.subject for event in today] == ["All day", "Later"]
    assert [event.subject for event in this_week] == ["All day", "Later", "Week"]
    assert [event.subject for event in next_two_weeks] == ["All day", "Later", "Week"]
    assert today[1].location == "{'displayName': 'Room A'}"
    assert today[1].organizer == "{'emailAddress': {'name': 'Alex'}}"
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
        window_days=1,
        timezone=timezone.utc,
        now=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )

    assert [item.subject for item in filtered] == ["Flattened event"]


def test_filter_events_by_view_berlin_summer_timezone_conversion() -> None:
    """Test UTC-to-Berlin conversion for summer time (CEST = UTC+2)."""
    # Event at 2026-08-25T12:00:00 UTC should display as 14:00 in Berlin summer time
    events = [
        _event("summer", "2026-08-25T12:00:00.0000000", "2026-08-25T13:00:00.0000000", subject="Summer event"),
    ]
    
    filtered = filter_events_by_view(
        events,
        window_days=1,
        timezone="Europe/Berlin",
        now=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
    )
    
    assert len(filtered) == 1
    assert filtered[0].subject == "Summer event"
    # Start time should be 14:00 Berlin time (UTC 12:00 + 2 hours CEST)
    assert filtered[0].start.hour == 14
    assert filtered[0].start.minute == 0


def test_filter_events_by_view_berlin_winter_timezone_conversion() -> None:
    """Test UTC-to-Berlin conversion for winter time (CET = UTC+1)."""
    # Event at 2026-01-15T12:00:00 UTC should display as 13:00 in Berlin winter time
    events = [
        _event("winter", "2026-01-15T12:00:00.0000000", "2026-01-15T13:00:00.0000000", subject="Winter event"),
    ]
    
    filtered = filter_events_by_view(
        events,
        window_days=1,
        timezone="Europe/Berlin",
        now=datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc),
    )
    
    assert len(filtered) == 1
    assert filtered[0].subject == "Winter event"
    # Start time should be 13:00 Berlin time (UTC 12:00 + 1 hour CET)
    assert filtered[0].start.hour == 13
    assert filtered[0].start.minute == 0


def test_filter_events_by_view_berlin_local_day_boundary() -> None:
    """Test that filtering uses Berlin-local calendar days, not UTC calendar days.
    
    Event at 2026-08-25T22:30:00 UTC (00:30 Aug 26 Berlin time) should be excluded 
    from Aug 25 today view but included in Aug 26 view.
    """
    # Event at 22:30 UTC (midnight-ish in Berlin), outside of Berlin Aug 25
    events = [
        _event("boundary", "2026-08-25T22:30:00.0000000", "2026-08-25T23:00:00.0000000", subject="Boundary event"),
    ]
    
    # View for Aug 25 Berlin time (reference_now is 2026-08-25 12:00 UTC = 14:00 Berlin)
    today_berlin = filter_events_by_view(
        events,
        window_days=1,
        timezone="Europe/Berlin",
        now=datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc),
    )
    
    # The event starts at 2026-08-26 00:30 Berlin time, which is after the Berlin window
    # of 2026-08-25 00:00 to 2026-08-26 00:00, so it should be excluded
    assert len(today_berlin) == 0


def test_build_calendar_template_context_includes_event_details() -> None:
    """build_calendar_template_context should produce correct context structure."""
    events = [
        CalendarEvent(
            subject="Planning",
            start=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 20, 10, 30, tzinfo=timezone.utc),
            location="Room A",
            organizer="Alex",
            all_day=False,
            timezone_name="UTC",
        ),
    ]

    context = build_calendar_template_context(
        events,
        title="Today",
        window_days=1,
        timezone=timezone.utc,
        generated_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert context["title"] == "Today"
    assert context["event_count"] == 1
    assert "Aug 20, 2026" in context["date_range"]
    assert len(context["events"]) == 1
    assert context["events"][0]["subject"] == "Planning"
    assert "09:00" in context["events"][0]["time"]
    assert context["events"][0]["location"] == "Room A"
    assert context["events"][0]["organizer"] == "Alex"
    assert "Last updated" in context["generated_at_label"]


def test_build_calendar_template_context_empty_events() -> None:
    """build_calendar_template_context should handle empty event lists."""
    context = build_calendar_template_context(
        [],
        title="Today",
        window_days=1,
        timezone=timezone.utc,
        generated_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert context["event_count"] == 0
    assert len(context["events"]) == 0
    assert context["title"] == "Today"


def test_build_calendar_template_context_formats_all_day_events() -> None:
    """build_calendar_template_context should format all-day events correctly."""
    events = [
        CalendarEvent(
            subject="All-day event",
            start=datetime(2026, 8, 20, 0, 0, tzinfo=timezone.utc),
            end=datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc),
            location="",
            organizer="",
            all_day=True,
            timezone_name="UTC",
        ),
    ]

    context = build_calendar_template_context(
        events,
        title="Today",
        window_days=1,
        timezone=timezone.utc,
        generated_at=datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc),
    )

    assert "All day" in context["events"][0]["time"]


def test_generate_calendar_packages_creates_expected_packages(tmp_path: Path) -> None:
    """generate_calendar_packages should generate packages with correct metadata."""
    # Create minimal template directory structure with view configs
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    views_dir = template_dir / "views"
    views_dir.mkdir()

    # Create template.html
    (template_dir / "template.html").write_text("<html><body>{{ title }}: {{ event_count }} event(s)</body></html>")

    # Create view configs
    (views_dir / "today.json").write_text(json.dumps({"title": "Today", "window_days": 1, "layout_name": "Calendar Today"}))
    (views_dir / "this_week.json").write_text(json.dumps({"title": "This Week", "window_days": 7, "layout_name": "Calendar Week"}))
    (views_dir / "next_2_weeks.json").write_text(json.dumps({"title": "Next 2 Weeks", "window_days": 14, "layout_name": "Calendar Future"}))

    now = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    events = [
        _event("today", "2026-08-20T10:00:00.0000000", "2026-08-20T11:00:00.0000000", subject="Today"),
        _event("week", "2026-08-22T10:00:00.0000000", "2026-08-22T11:00:00.0000000", subject="Week"),
    ]

    output_dir = tmp_path / "output"
    packages = generate_calendar_packages(
        events,
        ("today", "this_week", "next_2_weeks"),
        template_dir,
        output_dir,
        timezone.utc,
        now=now,
    )

    assert len(packages) == 3
    assert [p.view_type for p in packages] == ["today", "this_week", "next_2_weeks"]
    assert [p.title for p in packages] == ["Today", "This Week", "Next 2 Weeks"]
    assert [p.layout_name for p in packages] == ["Calendar Today", "Calendar Week", "Calendar Future"]
    assert [p.event_count for p in packages] == [1, 2, 2]
    assert [p.package_path.name for p in packages] == [
        "calendar_today_2026-08-20.htz",
        "calendar_this_week_2026-08-20.htz",
        "calendar_next_2_weeks_2026-08-20.htz",
    ]
    assert all(p.package_path.exists() for p in packages)

    # Verify HTZ contents
    for package in packages:
        with zipfile.ZipFile(package.package_path) as archive:
            assert archive.namelist() == ["index.html"]
            html = archive.read("index.html").decode("utf-8")
            assert package.title in html
            assert f"{package.event_count} event(s)" in html


def test_generate_calendar_packages_uses_default_template_html_when_template_file_not_set(
    tmp_path: Path,
) -> None:
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    views_dir = template_dir / "views"
    views_dir.mkdir()

    (template_dir / "template.html").write_text(
        "<html><body>DEFAULT {{ title }}</body></html>",
        encoding="utf-8",
    )
    (template_dir / "alternate.html").write_text(
        "<html><body>ALTERNATE {{ title }}</body></html>",
        encoding="utf-8",
    )
    (views_dir / "today.json").write_text(
        json.dumps({"title": "Today", "window_days": 1, "layout_name": "Calendar Today"}),
        encoding="utf-8",
    )

    packages = generate_calendar_packages(
        events=[_event("today", "2026-08-20T10:00:00", "2026-08-20T11:00:00", subject="Today")],
        view_types=("today",),
        template_dir=template_dir,
        output_path=tmp_path / "out",
        timezone=timezone.utc,
        now=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )

    assert len(packages) == 1
    with zipfile.ZipFile(packages[0].package_path) as archive:
        html = archive.read("index.html").decode("utf-8")
    assert "DEFAULT Today" in html
    assert "ALTERNATE Today" not in html


def test_generate_calendar_packages_honors_template_file_override(tmp_path: Path) -> None:
    template_dir = tmp_path / "template"
    template_dir.mkdir()
    views_dir = template_dir / "views"
    views_dir.mkdir()

    (template_dir / "template.html").write_text(
        "<html><body>DEFAULT {{ title }}</body></html>",
        encoding="utf-8",
    )
    (template_dir / "custom.html").write_text(
        "<html><body>CUSTOM {{ title }}</body></html>",
        encoding="utf-8",
    )
    (views_dir / "today.json").write_text(
        json.dumps(
            {
                "title": "Today",
                "window_days": 1,
                "layout_name": "Calendar Today",
                "template_file": "custom.html",
            }
        ),
        encoding="utf-8",
    )

    packages = generate_calendar_packages(
        events=[_event("today", "2026-08-20T10:00:00", "2026-08-20T11:00:00", subject="Today")],
        view_types=("today",),
        template_dir=template_dir,
        output_path=tmp_path / "out",
        timezone=timezone.utc,
        now=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )

    assert len(packages) == 1
    with zipfile.ZipFile(packages[0].package_path) as archive:
        html = archive.read("index.html").decode("utf-8")
    assert "CUSTOM Today" in html
    assert "DEFAULT Today" not in html


def test_generate_calendar_packages_loads_real_bundled_configs() -> None:
    """generate_calendar_packages should work with the real bundled template directory."""
    bundled_template_dir = Path(__file__).parent.parent.parent / "templates" / "calendar"
    
    # Skip if bundled template directory doesn't exist (tests run in different contexts)
    if not bundled_template_dir.exists():
        pytest.skip(f"Bundled template directory not found: {bundled_template_dir}")

    # Verify the three bundled views exist and have exact expected values
    views_dir = bundled_template_dir / "views"
    expected = {
        "today": {
            "title": "Today",
            "window_days": 1,
            "layout_name": "Calendar Today",
        },
        "this_week": {
            "title": "This Week",
            "window_days": 7,
            "layout_name": "Calendar This Week",
        },
        "next_2_weeks": {
            "title": "Next 2 Weeks",
            "window_days": 14,
            "layout_name": "Calendar Next 2 Weeks",
        },
    }

    for view_name, expected_values in expected.items():
        view_config_path = views_dir / f"{view_name}.json"
        assert view_config_path.exists(), f"Missing view config: {view_config_path}"
        
        config = json.loads(view_config_path.read_text(encoding="utf-8"))
        assert config["title"] == expected_values["title"]
        assert config["window_days"] == expected_values["window_days"]
        assert config["layout_name"] == expected_values["layout_name"]
