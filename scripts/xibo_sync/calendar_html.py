"""Generate self-contained HTML calendar packages from Microsoft Graph events."""

from __future__ import annotations

import html
import logging
import re
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

VIEW_TITLES = {
    "today": "Today",
    "this_week": "This Week",
    "next_2_weeks": "Next 2 Weeks",
}

VIEW_WINDOWS_DAYS = {
    "today": 1,
    "this_week": 7,
    "next_2_weeks": 14,
}


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


_FRACTION_RE = re.compile(r"\.(\d{6})\d+")


def _coerce_timezone(value: str | tzinfo | None) -> tzinfo:
    if value is None:
        return timezone.utc
    if isinstance(value, tzinfo):
        return value
    return ZoneInfo(str(value))


def _format_date(value: date) -> str:
    return f"{value:%b} {value.day}, {value:%Y}"


def _view_spec(view_type: str) -> tuple[str, int]:
    try:
        return VIEW_TITLES[view_type], VIEW_WINDOWS_DAYS[view_type]
    except KeyError as exc:
        raise ValueError(f"Unknown calendar view: {view_type}") from exc


def _view_window(view_type: str, base_time: datetime) -> tuple[datetime, datetime]:
    _, days = _view_spec(view_type)
    window_start = base_time.replace(hour=0, minute=0, second=0, microsecond=0)
    return window_start, window_start + timedelta(days=days)


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
    location = _text(_nested(event, "location", "displayName")).strip()
    organizer = _text(_nested(event, "organizer", "emailAddress", "name")).strip()
    all_day = bool(event.get("isAllDay")) or (
        start.time() == datetime.min.time() and end.time() == datetime.min.time() and (end - start) >= timedelta(days=1)
    )

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
    view_type: str,
    timezone: str | tzinfo | None = "UTC",
    *,
    now: datetime | None = None,
) -> list[CalendarEvent]:
    """Return events that belong in the requested calendar view."""

    reference_timezone = _coerce_timezone(timezone)
    reference_now = now or datetime.now(reference_timezone)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=reference_timezone)
    else:
        reference_now = reference_now.astimezone(reference_timezone)

    window_start, window_end = _view_window(view_type, reference_now)
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


def render_calendar_html(
    events: Sequence[CalendarEvent],
    view_type: str,
    timezone: str | tzinfo | None = "UTC",
    *,
    generated_at: datetime | None = None,
) -> str:
    """Render a complete HTML document for a calendar view."""

    title, days = _view_spec(view_type)
    reference_timezone = _coerce_timezone(timezone)
    generated_at = generated_at or datetime.now(reference_timezone)
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=reference_timezone)
    else:
        generated_at = generated_at.astimezone(reference_timezone)

    window_start = generated_at.replace(hour=0, minute=0, second=0, microsecond=0)
    window_end = window_start + timedelta(days=days)
    date_range = _format_range(window_start, window_end)

    rendered_items: list[str] = []
    for event in events:
        start_label = _format_event_time(event, reference_timezone)
        details: list[str] = []
        if event.location:
            details.append(f"<span class=\"meta-item\">{html.escape(event.location)}</span>")
        if event.organizer:
            details.append(f"<span class=\"meta-item\">{html.escape(event.organizer)}</span>")

        rendered_items.append(
            "\n".join(
                [
                    "<article class=\"event-card\">",
                    f"  <div class=\"event-time\">{html.escape(start_label)}</div>",
                    f"  <div class=\"event-subject\">{html.escape(event.subject)}</div>",
                    f"  <div class=\"event-meta\">{''.join(details)}</div>",
                    "</article>",
                ]
            )
        )

    body_content = (
        "\n".join(rendered_items)
        if rendered_items
        else "<div class=\"empty-state\">No events scheduled</div>"
    )

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Calendar - {html.escape(title)}</title>
  <style>
    :root {{
      --bg: #f7f9fc;
      --surface: #ffffff;
      --surface-alt: #eef3f8;
      --text: #132238;
      --muted: #5d6b7f;
      --accent: #2b6cb0;
      --border: rgba(19, 34, 56, 0.12);
      --shadow: 0 16px 40px rgba(19, 34, 56, 0.08);
    }}
    @media (prefers-color-scheme: dark) {{
      :root {{
        --bg: #08111f;
        --surface: #102033;
        --surface-alt: #16273e;
        --text: #edf3fb;
        --muted: #9fb0c4;
        --accent: #84b6ff;
        --border: rgba(255, 255, 255, 0.12);
        --shadow: 0 16px 40px rgba(0, 0, 0, 0.35);
      }}
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      font-family: "Segoe UI", Arial, sans-serif;
      background: linear-gradient(180deg, var(--bg), var(--surface-alt));
      color: var(--text);
    }}
    .page {{
      min-height: 100vh;
      padding: 40px;
      display: flex;
      flex-direction: column;
      gap: 24px;
    }}
    .header, .footer {{
      background: color-mix(in srgb, var(--surface) 88%, transparent);
      border: 1px solid var(--border);
      border-radius: 24px;
      box-shadow: var(--shadow);
      padding: 28px 32px;
    }}
    .header h1 {{
      margin: 0 0 8px;
      font-size: clamp(2rem, 4vw, 3.25rem);
      letter-spacing: -0.03em;
    }}
    .header .subtitle {{
      margin: 0;
      color: var(--muted);
      font-size: clamp(1rem, 1.6vw, 1.25rem);
    }}
    .stats {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      margin-top: 18px;
    }}
    .pill {{
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 8px 14px;
      background: var(--surface-alt);
      color: var(--accent);
      font-weight: 600;
    }}
    .list {{
      display: grid;
      gap: 16px;
    }}
    .event-card {{
      background: var(--surface);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 22px 24px;
      box-shadow: var(--shadow);
      display: grid;
      gap: 10px;
    }}
    .event-time {{
      color: var(--accent);
      font-size: 1.05rem;
      font-weight: 700;
    }}
    .event-subject {{
      font-size: clamp(1.4rem, 2vw, 2rem);
      font-weight: 700;
      line-height: 1.15;
    }}
    .event-meta {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      color: var(--muted);
    }}
    .meta-item {{
      display: inline-flex;
      align-items: center;
      padding: 7px 12px;
      border-radius: 999px;
      background: var(--surface-alt);
    }}
    .empty-state {{
      border: 2px dashed var(--border);
      border-radius: 20px;
      padding: 42px 24px;
      text-align: center;
      color: var(--muted);
      font-size: clamp(1.2rem, 2vw, 1.8rem);
      background: var(--surface);
    }}
    .footer {{
      color: var(--muted);
      font-size: 0.95rem;
    }}
    @media (max-width: 900px) {{
      .page {{ padding: 20px; }}
      .header, .footer {{ padding: 22px; border-radius: 18px; }}
      .event-card {{ padding: 18px 20px; border-radius: 16px; }}
    }}
  </style>
</head>
<body>
  <main class="page">
    <section class="header">
      <h1>Calendar - {html.escape(title)}</h1>
      <p class="subtitle">{html.escape(date_range)}</p>
      <div class="stats">
        <span class="pill">{len(events)} event(s)</span>
      </div>
    </section>
    <section class="list">
      {body_content}
    </section>
    <section class="footer">
      Last updated: {html.escape(generated_at.strftime("%Y-%m-%d %H:%M %Z"))}
    </section>
  </main>
</body>
</html>
"""


def package_html_to_zip(html_content: str, output_path: Path, package_name: str) -> Path:
    """Write a ZIP-based HTZ package containing the supplied HTML."""

    output_path.mkdir(parents=True, exist_ok=True)
    file_name = package_name if package_name.lower().endswith(".htz") else f"{package_name}.htz"
    package_path = output_path / file_name

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html_content)

    return package_path


def generate_calendar_packages(
    events: Iterable[dict[str, Any]],
    view_types: Sequence[str],
    output_path: Path,
    timezone: str | tzinfo | None = "UTC",
    *,
    now: datetime | None = None,
) -> list[CalendarPackage]:
    """Generate one package per requested view and write them to disk."""

    reference_timezone = _coerce_timezone(timezone)
    reference_now = now or datetime.now(reference_timezone)
    if reference_now.tzinfo is None:
        reference_now = reference_now.replace(tzinfo=reference_timezone)
    else:
        reference_now = reference_now.astimezone(reference_timezone)

    event_list = list(events)
    packages: list[CalendarPackage] = []

    for view_type in view_types:
        filtered_events = filter_events_by_view(
            event_list,
            view_type,
            reference_timezone,
            now=reference_now,
        )
        html_content = render_calendar_html(
            filtered_events,
            view_type,
            reference_timezone,
            generated_at=reference_now,
        )
        package_name = f"calendar_{view_type}_{reference_now.date():%Y-%m-%d}"
        package_path = package_html_to_zip(html_content, output_path, package_name)
        title, _ = _view_spec(view_type)
        window_start, window_end = _view_window(view_type, reference_now)
        packages.append(
            CalendarPackage(
                view_type=view_type,
                title=title,
                date_range=_format_range(window_start, window_end),
                event_count=len(filtered_events),
                package_path=package_path,
            )
        )

    return packages
