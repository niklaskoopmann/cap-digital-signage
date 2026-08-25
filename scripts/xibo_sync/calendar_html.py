"""Generate self-contained HTML calendar packages from Microsoft Graph events."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from . import html_packaging

@dataclass(frozen=True)
class CalendarEvent:
    """Normalized event data used for HTML rendering."""

    subject: str
    start: datetime
    end: datetime
    location: str = ""
    organizer: str = ""
    all_day: bool = False
    timezone_name: str = ""


@dataclass(frozen=True)
class CalendarPackage:
    """Metadata about a generated package on disk."""

    view_type: str
    title: str
    date_range: str
    event_count: int
    package_path: Path
    layout_name: str


_FRACTION_RE = re.compile(r"\.(\d{6})\d+")


def _coerce_timezone(value: str | tzinfo | None) -> tzinfo:
    if value is None:
        return timezone.utc
    if isinstance(value, tzinfo):
        return value
    return ZoneInfo(str(value))


def _format_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value:%Y}"


def _strip_fraction(value: str) -> str:
    return _FRACTION_RE.sub(r".\1", value)


def _parse_iso_datetime(value: str) -> datetime:
    cleaned = _strip_fraction(value.strip().replace("Z", "+00:00"))
    if len(cleaned) == 10:
        return datetime.fromisoformat(f"{cleaned}T00:00:00")
    return datetime.fromisoformat(cleaned)


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


def _event_timezone_name(event: dict[str, Any], default_timezone: str | tzinfo | None) -> str:
    candidate = _text(_nested(event, "start", "timeZone")).strip()
    if not candidate:
        candidate = _text(event.get("startTimeZone")).strip()
    if candidate:
        return candidate
    if isinstance(default_timezone, tzinfo):
        return getattr(default_timezone, "key", default_timezone.tzname(None) or "UTC")
    return _text(default_timezone) or "UTC"


def _event_datetime(event: dict[str, Any], key: str, default_timezone: tzinfo) -> datetime | None:
    container = event.get(key)
    if isinstance(container, dict):
        raw_value = _text(container.get("dateTime")).strip()
        tz_name = _text(container.get("timeZone")).strip()
    else:
        raw_value = _text(event.get(f"{key}DateTime")).strip()
        tz_name = _text(event.get(f"{key}TimeZone")).strip()
    if not raw_value:
        return None

    dt = _parse_iso_datetime(raw_value)
    if dt.tzinfo is None:
        if tz_name:
            try:
                dt = dt.replace(tzinfo=ZoneInfo(tz_name))
            except Exception:
                logging.warning("Unknown event timezone '%s'; using configured timezone", tz_name)
                dt = dt.replace(tzinfo=default_timezone)
        else:
            dt = dt.replace(tzinfo=default_timezone)
    return dt


def _normalize_event(event: dict[str, Any], default_timezone: tzinfo) -> CalendarEvent | None:
    start = _event_datetime(event, "start", default_timezone)
    end = _event_datetime(event, "end", default_timezone)
    if start is None or end is None:
        logging.warning("Skipping calendar event missing start/end data: %s", _text(event.get("id")))
        return None
    if end < start:
        logging.warning("Skipping calendar event with end before start: %s", _text(event.get("id")))
        return None

    subject = _text(event.get("subject")).strip() or "(No subject)"
    location = _text(event.get("location")).strip()
    organizer = _text(event.get("organizer")).strip()
    all_day = bool((_text(event.get("isAllDay")).strip().lower() == "true") or (
        start.time() == datetime.min.time() and end.time() == datetime.min.time() and (end - start) >= timedelta(days=1)
    ))

    return CalendarEvent(
        subject=subject,
        start=start,
        end=end,
        location=location,
        organizer=organizer,
        all_day=all_day,
        timezone_name=_event_timezone_name(event, default_timezone),
    )


def _format_duration(start: datetime, end: datetime) -> str:
    total_minutes = max(int((end - start).total_seconds() // 60), 0)
    hours, minutes = divmod(total_minutes, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


def _format_event_time(event: CalendarEvent, reference_timezone: tzinfo) -> str:
    if event.all_day:
        return "All day"
    start_local = event.start.astimezone(reference_timezone)
    duration = _format_duration(event.start, event.end)
    return f"{start_local:%H:%M} ({duration})"


def _format_range(window_start: datetime, window_end: datetime) -> str:
    start_date = window_start.date()
    end_date = (window_end - timedelta(days=1)).date()
    return f"{_format_date(start_date)} - {_format_date(end_date)}"


def filter_events_by_view(
    events: Iterable[dict[str, Any]],
    window_days: int,
    timezone: str | tzinfo | None = "UTC",
    *,
    now: datetime | None = None,
) -> list[CalendarEvent]:
    """Return events that belong in the requested calendar view.
    
    Args:
        events: Raw calendar event dicts from Microsoft Graph.
        window_days: Number of days in the view window (e.g., 1 for today, 7 for this week).
        timezone: Timezone for calculations.
        now: Reference timestamp (defaults to current time).
    """

    reference_timezone = _coerce_timezone(timezone)
    reference_now = now or datetime.now(reference_timezone)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=reference_timezone)
    else:
        reference_now = reference_now.astimezone(reference_timezone)

    # Compute window from window_days instead of looking up in VIEW_WINDOWS_DAYS
    window_start = reference_now.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=window_days)
    filtered: list[CalendarEvent] = []

    for raw_event in events:
        if not isinstance(raw_event, dict):
            logging.warning("Skipping non-object calendar event: %r", raw_event)
            continue

        normalized = _normalize_event(raw_event, reference_timezone)
        if normalized is None:
            continue

        if normalized.timezone_name and normalized.timezone_name != getattr(reference_timezone, "key", normalized.timezone_name):
            logging.warning(
                "Calendar event timezone '%s' differs from configured timezone; rendering with event timezone",
                normalized.timezone_name,
            )

        start_local = normalized.start.astimezone(reference_timezone)
        end_local = normalized.end.astimezone(reference_timezone)

        if end_local < reference_now:
            continue
        if start_local < window_start or start_local >= window_end:
            continue

        filtered.append(
            CalendarEvent(
                subject=normalized.subject,
                start=start_local,
                end=end_local,
                location=normalized.location,
                organizer=normalized.organizer,
                all_day=normalized.all_day,
                timezone_name=normalized.timezone_name,
            )
        )

    filtered.sort(key=lambda event: (event.start, event.subject.lower()))
    return filtered


def build_calendar_template_context(
    events: Sequence[CalendarEvent],
    title: str,
    window_days: int,
    timezone: str | tzinfo | None = "UTC",
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    """Build the Jinja2 context dict for a calendar view template.
    
    Args:
        events: Filtered calendar events for the view.
        title: View title (from config, e.g., 'Today', 'This Week').
        window_days: Number of days in the view window (from config).
        timezone: Timezone for formatting.
        generated_at: Timestamp to use (defaults to current time).
    
    Returns:
        Dict suitable for passing to html_packaging.render_template().
    """
    reference_timezone = _coerce_timezone(timezone)
    generated_at = generated_at or datetime.now(reference_timezone)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=reference_timezone)
    else:
        generated_at = generated_at.astimezone(reference_timezone)

    window_start = generated_at.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=window_days)
    date_range = _format_range(window_start, window_end)

    event_view_models: list[dict[str, str]] = []
    for event in events:
        start_label = _format_event_time(event, reference_timezone)
        event_view_models.append(
            {
                "subject": event.subject,
                "time": start_label,
                "location": event.location,
                "organizer": event.organizer,
            }
        )

    return {
        "title": title,
        "date_range": date_range,
        "event_count": len(events),
        "events": event_view_models,
        "generated_at_label": f"Last updated: {generated_at.strftime('%Y-%m-%d %H:%M %Z')}",
    }


def generate_calendar_packages(
    events: Iterable[dict[str, Any]],
    view_types: Sequence[str],
    template_dir: Path,
    output_path: Path,
    timezone: str | tzinfo | None = "UTC",
    *,
    now: datetime | None = None,
) -> list[CalendarPackage]:
    """Generate one package per requested view and write them to disk.
    
    Args:
        events: Raw calendar event dicts from Microsoft Graph.
        view_types: List of view names to generate (e.g., 'today', 'this_week').
        template_dir: Template directory containing template.html and views/*.json configs.
        output_path: Directory in which to write the .htz packages.
        timezone: Timezone for event filtering and formatting.
        now: Reference timestamp (defaults to current time).
    
    Returns:
        List of CalendarPackage metadata for the generated packages.
    """

    reference_timezone = _coerce_timezone(timezone)
    reference_now = now or datetime.now(reference_timezone)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=reference_timezone)
    else:
        reference_now = reference_now.astimezone(reference_timezone)

    event_list = list(events)
    packages: list[CalendarPackage] = []
    views_dir = template_dir / "views"

    for view_type in view_types:
        # Load view configuration
        view_config = html_packaging.load_view_config(views_dir, view_type)
        title = view_config.get("title", view_type)
        window_days = view_config.get("window_days", 1)
        layout_name = view_config.get("layout_name", f"Calendar {title}")
        template_file = view_config.get("template_file", "template.html")

        # Filter events for this view
        filtered_events = filter_events_by_view(
            event_list,
            window_days,
            reference_timezone,
            now=reference_now,
        )

        # Build template context
        context = build_calendar_template_context(
            filtered_events,
            title,
            window_days,
            reference_timezone,
            generated_at=reference_now,
        )

        # Render template
        html_content = html_packaging.render_template(template_dir, template_file, context)

        # Package to HTZ
        package_name = f"calendar_{view_type}_{reference_now.date():%Y-%m-%d}"
        package_path = html_packaging.package_html_to_zip(html_content, output_path, package_name)

        # Compute date range for metadata
        window_start = reference_now.replace(hour=0, minute=0, second=0, microsecond=0)
        window_end = window_start + timedelta(days=window_days)

        packages.append(
            CalendarPackage(
                view_type=view_type,
                title=title,
                date_range=_format_range(window_start, window_end),
                event_count=len(filtered_events),
                package_path=package_path,
                layout_name=layout_name,
            )
        )

    return packages
