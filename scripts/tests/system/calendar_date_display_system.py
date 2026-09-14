"""Live Xibo system test for calendar date display rules.

Run explicitly from ``scripts/``; this module is intentionally excluded from
default pytest collection because it uploads test-owned media to the CMS.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, time, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(SCRIPTS_DIR))

from xibo_sync.calendar_html import generate_calendar_images
from xibo_sync.html_packaging import PlaywrightRenderer


def _items(payload: object) -> list[dict]:
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, dict):
        payload = payload.get("rows", [payload])
    if not isinstance(payload, list):
        raise AssertionError(f"Unexpected CMS response shape: {type(payload).__name__}")
    return [item for item in payload if isinstance(item, dict)]


def _tags(resource: dict) -> set[str]:
    values = resource.get("tags", resource.get("tag", []))
    if isinstance(values, str):
        return {value.strip() for value in values.split(",") if value.strip()}
    if isinstance(values, dict):
        values = [values]
    return {
        str(value.get("tag") or value.get("name") or value.get("value") or value).strip()
        for value in values or []
    }


class CmsApi:
    """System-test-owned client for media state checks and cleanup."""

    def __init__(self, selected_env: dict[str, str]) -> None:
        self.base_url = selected_env["CMS_BASE_URL"].rstrip("/")
        self.verify = selected_env.get("CMS_VERIFY_TLS", "false").lower() == "true"
        self.timeout = int(selected_env.get("CMS_TIMEOUT_SECONDS", "30"))
        self.session = requests.Session()
        if selected_env.get("AUTH_MODE", "oauth").lower() != "oauth":
            raise RuntimeError("System tests require AUTH_MODE=oauth in SYSTEM_TEST_ENV_FILE")
        response = self.session.post(
            f"{self.base_url}/api/authorize/access_token",
            data={
                "grant_type": "client_credentials",
                "client_id": selected_env.get("CMS_CLIENT_ID", ""),
                "client_secret": selected_env.get("CMS_CLIENT_SECRET", ""),
            },
            timeout=self.timeout,
            verify=self.verify,
        )
        response.raise_for_status()
        self.session.headers["Authorization"] = f"Bearer {response.json()['access_token']}"

    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        response = self.session.request(
            method,
            f"{self.base_url}/api{path}",
            timeout=self.timeout,
            verify=self.verify,
            **kwargs,
        )
        response.raise_for_status()
        return response

    def media_by_tag(self, tag: str) -> list[dict]:
        payload = self.request(
            "GET",
            "/library",
            params={"tags": tag, "exactTags": 1, "embed": "tags"},
        ).json()
        return [item for item in _items(payload) if tag in _tags(item)]

    def media_by_id(self, media_id: str) -> dict:
        items = _items(
            self.request("GET", "/library", params={"mediaId": media_id, "embed": "tags"}).json()
        )
        if len(items) != 1:
            raise AssertionError(f"Expected mediaId={media_id}, found {len(items)} records")
        return items[0]

    def delete_media(self, media_id: str) -> None:
        self.request("DELETE", f"/library/{media_id}", data={"forceDelete": 1})


def _load_selected_env() -> dict[str, str]:
    raw_path = os.environ.get("SYSTEM_TEST_ENV_FILE", "").strip()
    if not raw_path:
        raise RuntimeError("SYSTEM_TEST_ENV_FILE must name a test-only .env file for the local Docker CMS")
    path = Path(raw_path).expanduser().resolve()
    if path == (SCRIPTS_DIR / ".env").resolve():
        raise RuntimeError("SYSTEM_TEST_ENV_FILE must not point to scripts/.env")
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    endpoint = values.get("CMS_BASE_URL", "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port not in {None, 80}:
        raise RuntimeError("SYSTEM_TEST_ENV_FILE must set CMS_BASE_URL=http://localhost")
    if not values.get("CMS_CLIENT_ID") or not values.get("CMS_CLIENT_SECRET"):
        raise RuntimeError("SYSTEM_TEST_ENV_FILE is missing local CMS OAuth credentials")
    return values


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def _date_label(value: datetime) -> str:
    return f"{value:%b} {value.day}, {value:%Y}"


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"Generated file is not a PNG: {path}")
    return struct.unpack(">II", data[16:24])


def _event_time_texts(rendered_html: str) -> list[str]:
    return [
        fragment.split("</div>", 1)[0]
        for fragment in rendered_html.split('<div class="event-time">')[1:]
    ]


def _redact(output: str, selected_env: dict[str, str]) -> str:
    for key in ("CMS_CLIENT_ID", "CMS_CLIENT_SECRET"):
        value = selected_env.get(key)
        if value:
            output = output.replace(value, "<redacted>")
    return output


def _run_scenario(api: CmsApi, selected_env: dict[str, str], root: Path, token: str) -> list[str]:
    timezone = ZoneInfo("Europe/Berlin")
    now = datetime.now(timezone)
    today_start = datetime.combine(now.date(), time.min, timezone)
    tomorrow_start = today_start + timedelta(days=1)
    today_event_start = (now + timedelta(minutes=30)).replace(second=0, microsecond=0)
    today_event_end = today_event_start + timedelta(hours=1)
    one_day_view = f"system_one_day_{token}"
    multi_day_view = f"system_multi_day_{token}"
    managed_tag = f"system-test-calendar-dates-{token}"
    media_dir = root / "media"
    direct_media_dir = root / "direct-media"
    snapshot_dir = root / "snapshot"
    isolated_scripts = root / "scripts"
    template_dir = isolated_scripts / "templates" / "calendar"
    media_dir.mkdir(parents=True)
    direct_media_dir.mkdir(parents=True)
    snapshot_dir.mkdir(parents=True)
    isolated_scripts.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "sync_xibo.py", isolated_scripts / "sync_xibo.py")
    shutil.copytree(SCRIPTS_DIR / "xibo_sync", isolated_scripts / "xibo_sync")
    shutil.copytree(SCRIPTS_DIR / "templates" / "calendar", template_dir)
    (template_dir / "views" / f"{one_day_view}.json").write_text(
        json.dumps({"title": "System Test One Day", "window_days": 1}),
        encoding="utf-8",
    )
    (template_dir / "views" / f"{multi_day_view}.json").write_text(
        json.dumps({"title": "System Test Multi Day", "window_days": 3}),
        encoding="utf-8",
    )

    events = [
        {
            "id": f"today-{token}",
            "subject": f"Today system event {token}",
            "start": {"dateTime": today_event_start.isoformat(), "timeZone": "Europe/Berlin"},
            "end": {"dateTime": today_event_end.isoformat(), "timeZone": "Europe/Berlin"},
            "isCancelled": False,
        },
        {
            "id": f"tomorrow-{token}",
            "subject": f"Tomorrow system event {token}",
            "start": {"dateTime": tomorrow_start.replace(hour=0, minute=30).isoformat(), "timeZone": "Europe/Berlin"},
            "end": {"dateTime": tomorrow_start.replace(hour=1, minute=30).isoformat(), "timeZone": "Europe/Berlin"},
            "isCancelled": False,
        },
    ]
    (snapshot_dir / f"office_calendar_events_{now:%Y-%m-%d_%H-%M-%S}.json").write_text(
        json.dumps({"value": events}),
        encoding="utf-8",
    )

    rendered_html: dict[str, str] = {}
    playwright_renderer = PlaywrightRenderer()

    def capture_renderer(html: str, path: Path, width: int, height: int) -> None:
        rendered_html[path.name] = html
        playwright_renderer(html, path, width, height)

    direct_images = generate_calendar_images(
        events,
        (one_day_view, multi_day_view),
        template_dir,
        direct_media_dir,
        timezone,
        now=now,
        renderer=capture_renderer,
    )
    assert len(direct_images) == 2
    for image in direct_images:
        assert _png_dimensions(image.image_path) == (1920, 1080)
        assert image.image_path.stat().st_size > 1000, f"Rendered PNG is unexpectedly small: {image.image_path}"

    today_label = _date_label(today_start)
    tomorrow_label = _date_label(tomorrow_start)
    range_end_label = _date_label(today_start + timedelta(days=2))
    one_html = rendered_html[direct_images[0].image_path.name]
    one_event_times = _event_time_texts(one_html)
    assert f'<p class="subtitle">{today_label}</p>' in one_html
    assert f'<p class="subtitle">{today_label} - {today_label}</p>' not in one_html
    assert len(one_event_times) == 1
    assert today_label not in one_event_times[0]
    assert f"{today_event_start:%H:%M} (1h)" in one_event_times[0]

    multi_html = rendered_html[direct_images[1].image_path.name]
    multi_event_times = _event_time_texts(multi_html)
    assert f'<p class="subtitle">{today_label} - {range_end_label}</p>' in multi_html
    assert len(multi_event_times) == 2
    assert f"{today_label} | {today_event_start:%H:%M} (1h)" in multi_event_times[0]
    assert f"{tomorrow_label} | 00:30 (1h)" in multi_event_times[1]

    scenario_env = dict(selected_env)
    scenario_env.update(
        {
            "LOCAL_MEDIA_DIR": str(media_dir),
            "CALENDAR_JSON_PATH": str(snapshot_dir),
            "CALENDAR_ENABLE_HTML": "true",
            "CALENDAR_HTML_VIEWS": f"{one_day_view},{multi_day_view}",
            "CALENDAR_TEMPLATE_DIR": str(template_dir),
            "CALENDAR_TIMEZONE": "Europe/Berlin",
            "MANAGED_TAG": managed_tag,
            "MANAGED_FOLDER_ID": "",
            "DRY_RUN": "false",
            "DELETE_REMOTE_NOT_LOCAL": "false",
            "CREATE_LAYOUT_PER_UPLOAD": "false",
            "ASSIGN_LAYOUT_ON_CHANGE": "false",
            "IMMEDIATE_SHOW_ON_CHANGE": "false",
            "DISPLAY_GROUP_ID": "",
            "TRIGGER_COLLECTNOW_ON_CHANGES": "false",
            "LOG_FILE": "",
        }
    )
    _write_env(isolated_scripts / ".env", scenario_env)
    assert not api.media_by_tag(managed_tag), f"Pre-existing media uses test tag {managed_tag}"

    process_env = os.environ.copy()
    process_env.update(scenario_env)
    process_env["PYTHONIOENCODING"] = "utf-8"
    process_env["PYTHONUTF8"] = "1"
    completed = subprocess.run(
        [sys.executable, str(isolated_scripts / "sync_xibo.py"), "--upload-calendar-html", "--yes"],
        cwd=isolated_scripts,
        env=process_env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    output = completed.stdout + completed.stderr
    if completed.returncode != 0:
        raise AssertionError(
            f"Non-dry-run command failed ({completed.returncode}):\n{_redact(output[-6000:], selected_env)}"
        )

    generated_pngs = sorted(media_dir.glob("calendar_*.png"))
    assert len(generated_pngs) == 2, f"Expected two generated PNGs, found {generated_pngs}"
    for path in generated_pngs:
        assert _png_dimensions(path) == (1920, 1080)
        assert path.stat().st_size > 1000, f"Rendered PNG is unexpectedly small: {path}"

    media = api.media_by_tag(managed_tag)
    assert len(media) == 2, f"Expected two tagged media items, found {len(media)}"
    media_ids: list[str] = []
    expected_views = {one_day_view, multi_day_view}
    observed_views: set[str] = set()
    for item in media:
        media_id = str(item.get("mediaId") or item.get("id") or "")
        persisted = api.media_by_id(media_id)
        item_tags = _tags(item)
        view_tags = {tag.removeprefix("calendar-") for tag in item_tags if tag.startswith("calendar-system_")}
        observed_views.update(view_tags)
        assert {managed_tag, "calendar-html", "calendar-image"} <= item_tags
        assert str(persisted.get("mediaId") or persisted.get("id") or "") == media_id
        assert str(persisted.get("name") or persisted.get("fileName") or "").endswith(".png")
        media_ids.append(media_id)
    assert observed_views == expected_views
    return media_ids


def main() -> int:
    selected_env = _load_selected_env()
    api = CmsApi(selected_env)
    api.request("GET", "/library", params={"start": 0, "length": 1})
    token = uuid.uuid4().hex[:10]
    managed_tag = f"system-test-calendar-dates-{token}"
    cleanup_errors: list[str] = []

    try:
        with tempfile.TemporaryDirectory(prefix="xibo-calendar-dates-system-") as temp_dir:
            _run_scenario(api, selected_env, Path(temp_dir), token)
    finally:
        try:
            for media in api.media_by_tag(managed_tag):
                api.delete_media(str(media.get("mediaId") or media.get("id") or ""))
            assert not api.media_by_tag(managed_tag)
        except Exception as exc:
            cleanup_errors.append(f"{managed_tag}: {exc}")

    if cleanup_errors:
        raise RuntimeError("Cleanup verification failed: " + "; ".join(cleanup_errors))
    print(f"SYSTEM TEST PASS: token={token}; date display, PNG uploads, API state, and cleanup verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())