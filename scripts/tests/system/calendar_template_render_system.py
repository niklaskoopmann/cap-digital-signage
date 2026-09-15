"""Local Playwright layout checks for the bundled calendar template.

Run from ``scripts/`` with the repository virtual environment active. This test does not contact
Xibo or require CMS credentials.
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from xibo_sync import html_packaging
from xibo_sync.calendar_html import CalendarEvent, build_calendar_template_context, generate_calendar_images


TEMPLATE_DIR = Path(__file__).resolve().parents[2] / "templates" / "calendar"


def _png_dimensions_and_pixel(png: bytes, x: int, y: int) -> tuple[tuple[int, int], tuple[int, ...]]:
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    width, height, bit_depth, color_type = struct.unpack(">IIBB", png[16:26])
    assert bit_depth == 8 and color_type in {2, 6}
    idat = b""
    offset = 8
    while offset < len(png):
        length = struct.unpack(">I", png[offset:offset + 4])[0]
        chunk = png[offset + 4:offset + 8]
        payload = png[offset + 8:offset + 8 + length]
        if chunk == b"IDAT":
            idat += payload
        offset += 12 + length
    raw = zlib.decompress(idat)
    channels = 4 if color_type == 6 else 3
    stride = width * channels
    rows: list[bytes] = []
    cursor = 0
    previous = bytearray(stride)
    for _ in range(height):
        filter_type = raw[cursor]
        cursor += 1
        row = bytearray(raw[cursor:cursor + stride])
        cursor += stride
        for index in range(stride):
            left = row[index - channels] if index >= channels else 0
            above = previous[index]
            upper_left = previous[index - channels] if index >= channels else 0
            if filter_type == 1:
                row[index] = (row[index] + left) & 255
            elif filter_type == 2:
                row[index] = (row[index] + above) & 255
            elif filter_type == 3:
                row[index] = (row[index] + ((left + above) // 2)) & 255
            elif filter_type == 4:
                estimate = left + above - upper_left
                distances = (abs(estimate - left), abs(estimate - above), abs(estimate - upper_left))
                predictor = (left, above, upper_left)[distances.index(min(distances))]
                row[index] = (row[index] + predictor) & 255
            elif filter_type != 0:
                raise AssertionError(f"Unsupported PNG filter: {filter_type}")
        rows.append(bytes(row))
        previous = row
    pixel = rows[y][x * channels:(x + 1) * channels]
    return (width, height), tuple(pixel)


def _context(event_count: int) -> dict:
    events = [
        CalendarEvent(
            subject=f"System event {index}",
            start=datetime(2026, 8, 20, 9 + index, tzinfo=timezone.utc),
            end=datetime(2026, 8, 20, 9 + index, 30, tzinfo=timezone.utc),
            location="A deliberately long location value for wrapping checks",
            organizer="Calendar system test",
        )
        for index in range(event_count)
    ]
    return build_calendar_template_context(
        events,
        title="System test",
        window_days=1,
        timezone=timezone.utc,
        generated_at=datetime(2026, 8, 20, 8, tzinfo=timezone.utc),
    )


@pytest.mark.parametrize("event_count", [0, 1, 4, 5, 8, 9])
def test_calendar_template_fits_fixed_viewport(event_count: int, tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    html = html_packaging.render_template(TEMPLATE_DIR, "template.html", _context(event_count))
    output = tmp_path / f"calendar-{event_count}.png"

    with playwright.sync_playwright() as manager:
        browser = manager.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={"width": 1920, "height": 1080})
            page.set_content(html, wait_until="load")
            metrics = page.evaluate(
                """() => ({
                    width: document.documentElement.scrollWidth,
                    height: document.documentElement.scrollHeight,
                    cards: [...document.querySelectorAll('.event-card')].map(card => {
                        const rect = card.getBoundingClientRect();
                        return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom};
                    }),
                    list: (() => { const rect = document.querySelector('.list').getBoundingClientRect();
                        return {left: rect.left, right: rect.right, top: rect.top, bottom: rect.bottom}; })(),
                    columns: getComputedStyle(document.querySelector('.list')).gridTemplateColumns,
                    header: (() => { const rect = document.querySelector('.header').getBoundingClientRect();
                        return {top: rect.top, bottom: rect.bottom}; })(),
                    footer: (() => { const rect = document.querySelector('.footer').getBoundingClientRect();
                        return {top: rect.top, bottom: rect.bottom}; })(),
                    rows: [...new Set([...document.querySelectorAll('.event-card')].map(card =>
                        Math.round(card.getBoundingClientRect().top)))].length,
                    subjects: [...document.querySelectorAll('.event-subject')].map(subject => subject.textContent),
                    badge: document.querySelector('.pill')?.textContent?.trim() || ''
                })"""
            )
            screenshot = page.screenshot(path=str(output), type="png", full_page=False)
        finally:
            browser.close()

    assert metrics["width"] <= 1920
    assert metrics["height"] <= 1080
    assert len(metrics["cards"]) == min(event_count, 8)
    assert 0 <= metrics["header"]["top"] < metrics["header"]["bottom"] <= 1080
    assert 0 <= metrics["footer"]["top"] < metrics["footer"]["bottom"] <= 1080
    for card in metrics["cards"]:
        assert metrics["list"]["left"] <= card["left"] <= card["right"] <= metrics["list"]["right"]
        assert metrics["list"]["top"] <= card["top"] <= card["bottom"] <= metrics["list"]["bottom"]
    expected_columns = 1 if event_count <= 4 else 2
    assert len(metrics["columns"].split()) == expected_columns
    assert metrics["rows"] <= 4
    assert metrics["subjects"] == [f"System event {index}" for index in range(min(event_count, 8))]
    expected_badge = f"8 of {event_count} events" if event_count > 8 else f"{event_count} event"
    if event_count != 1 and event_count <= 8:
        expected_badge = f"{event_count} events"
    assert metrics["badge"] == expected_badge
    dimensions, pixel = _png_dimensions_and_pixel(screenshot, 10, 10)
    assert dimensions == (1920, 1080)
    assert pixel[:3] != (128, 128, 128)
    _, second_pixel = _png_dimensions_and_pixel(screenshot, 1910, 10)
    assert second_pixel[:3] != (128, 128, 128)


def test_generate_calendar_images_produces_bundled_background_png(tmp_path: Path) -> None:
    playwright = pytest.importorskip("playwright.sync_api")
    events = [
        {
            "id": f"event-{index}",
            "subject": f"Production event {index}",
            "start": {"dateTime": f"2026-08-20T{9 + index:02d}:00:00", "timeZone": "UTC"},
            "end": {"dateTime": f"2026-08-20T{9 + index:02d}:30:00", "timeZone": "UTC"},
        }
        for index in range(8)
    ]
    output = tmp_path / "calendar_today_2026-08-20.png"

    images = generate_calendar_images(
        events,
        ("today",),
        TEMPLATE_DIR,
        tmp_path,
        timezone="UTC",
        now=datetime(2026, 8, 20, 8, tzinfo=timezone.utc),
        renderer=html_packaging.PlaywrightRenderer(),
    )

    assert images[0].event_count == 8
    dimensions, pixel = _png_dimensions_and_pixel(output.read_bytes(), 10, 10)
    assert dimensions == (1920, 1080)
    assert pixel[:3] != (128, 128, 128)
