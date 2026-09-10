"""Live Xibo system test for calendar PNG uploads.

Run explicitly from ``scripts/``; this module is intentionally excluded from
the default pytest naming convention because it mutates the selected CMS.
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
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
DISPLAY_GROUP_ID = "2"


def _items(payload: object) -> list[dict]:
    if isinstance(payload, dict) and "data" in payload:
        payload = payload["data"]
    if isinstance(payload, dict):
        payload = payload.get("layouts", payload.get("rows", [payload]))
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


def _contains_id(payload: object, expected_id: str) -> bool:
    if isinstance(payload, dict):
        if any(str(payload.get(key) or "") == expected_id for key in ("layoutId", "id")):
            return True
        return any(_contains_id(value, expected_id) for value in payload.values())
    if isinstance(payload, list):
        return any(_contains_id(value, expected_id) for value in payload)
    return False


class CmsApi:
    """Minimal system-test-owned client for observable CMS state and cleanup."""

    def __init__(self, selected_env: dict[str, str]) -> None:
        self.base_url = selected_env["CMS_BASE_URL"].rstrip("/")
        self.verify = selected_env.get("CMS_VERIFY_TLS", "false").lower() == "true"
        self.timeout = int(selected_env.get("CMS_TIMEOUT_SECONDS", "30"))
        self.session = requests.Session()
        auth_mode = selected_env.get("AUTH_MODE", "oauth").lower()
        if auth_mode != "oauth":
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
        items = _items(self.request("GET", "/library", params={"tags": tag, "exactTags": 1, "embed": "tags"}).json())
        return [item for item in items if tag in _tags(item)]

    def layouts_by_tag(self, tag: str) -> list[dict]:
        items = _items(self.request("GET", "/layout", params={"tags": tag, "exactTags": 1, "embed": "tags"}).json())
        return [item for item in items if tag in _tags(item)]

    def layouts_by_name(self, name: str) -> list[dict]:
        return [
            item
            for item in _items(self.request("GET", "/layout", params={"layout": name, "showDrafts": 1}).json())
            if str(item.get("layout") or item.get("name") or "") == name
        ]

    def media_layout_usage(self, media_id: str) -> object:
        return self.request("GET", f"/library/usage/layouts/{media_id}").json()

    def unassign_layout(self, layout_id: str) -> None:
        self.request(
            "POST",
            f"/displaygroup/{DISPLAY_GROUP_ID}/layout/unassign",
            data=[("layoutId[]", layout_id)],
        )

    def delete_layout(self, layout_id: str) -> None:
        self.request("DELETE", f"/layout/{layout_id}")

    def delete_media(self, media_id: str) -> None:
        self.request("DELETE", f"/library/{media_id}", data={"forceDelete": 1})


def _load_selected_env() -> tuple[Path, dict[str, str]]:
    raw_path = os.environ.get("SYSTEM_TEST_ENV_FILE", "").strip()
    if not raw_path:
        raise RuntimeError("SYSTEM_TEST_ENV_FILE must name a test-only .env file for the local Docker CMS")
    path = Path(raw_path).expanduser().resolve()
    if path == (SCRIPTS_DIR / ".env").resolve():
        raise RuntimeError("SYSTEM_TEST_ENV_FILE must not point to scripts/.env")
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    endpoint = os.environ.get("SYSTEM_TEST_CMS_BASE_URL", values.get("CMS_BASE_URL", "")).strip()
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port not in {None, 80}:
        raise RuntimeError(
            "The effective system-test endpoint must be http://localhost; set it in "
            "SYSTEM_TEST_ENV_FILE or SYSTEM_TEST_CMS_BASE_URL"
        )
    if not values.get("CMS_CLIENT_ID") or not values.get("CMS_CLIENT_SECRET"):
        raise RuntimeError("SYSTEM_TEST_ENV_FILE is missing local CMS OAuth credentials")
    values["CMS_BASE_URL"] = endpoint
    return path, values


def _write_env(path: Path, values: dict[str, str]) -> None:
    path.write_text("".join(f"{key}={value}\n" for key, value in values.items()), encoding="utf-8")


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        raise AssertionError(f"Generated file is not a PNG: {path}")
    return struct.unpack(">II", data[16:24])


def _redact(output: str, selected_env: dict[str, str]) -> str:
    for key in ("CMS_CLIENT_ID", "CMS_CLIENT_SECRET"):
        value = selected_env.get(key)
        if value:
            output = output.replace(value, "<redacted>")
    return output


def _run_scenario(
    api: CmsApi,
    selected_env: dict[str, str],
    root: Path,
    token: str,
    *,
    create_layout: bool,
) -> dict[str, object]:
    mode = "enabled" if create_layout else "disabled"
    view = f"system_{mode}_{token}"
    managed_tag = f"system-test-calendar-{mode}-{token}"
    legacy_layout_name = f"SYSTEM TEST LEGACY {mode} {token}"
    media_dir = root / f"media-{mode}"
    snapshot_dir = root / f"snapshot-{mode}"
    isolated_scripts = root / f"scripts-{mode}"
    template_dir = isolated_scripts / "templates" / "calendar"
    snapshot_dir.mkdir(parents=True)
    media_dir.mkdir(parents=True)
    shutil.copy2(SCRIPTS_DIR / "sync_xibo.py", isolated_scripts / "sync_xibo.py")
    shutil.copytree(SCRIPTS_DIR / "xibo_sync", isolated_scripts / "xibo_sync")
    shutil.copytree(SCRIPTS_DIR / "templates" / "calendar", template_dir)
    (template_dir / "views" / f"{view}.json").write_text(
        json.dumps({"title": f"System Test {mode}", "window_days": 1, "layout_name": legacy_layout_name}),
        encoding="utf-8",
    )
    (snapshot_dir / f"system_test_{token}.json").write_text(
        json.dumps(
            {
                "value": [
                    {
                        "id": f"event-{token}",
                        "subject": f"System test {token}",
                        "start": {"dateTime": "2099-01-01T09:00:00", "timeZone": "UTC"},
                        "end": {"dateTime": "2099-01-01T10:00:00", "timeZone": "UTC"},
                        "isCancelled": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    scenario_env = dict(selected_env)
    scenario_env.update(
        {
            "LOCAL_MEDIA_DIR": str(media_dir),
            "CALENDAR_JSON_PATH": str(snapshot_dir),
            "CALENDAR_ENABLE_HTML": "true",
            "CALENDAR_HTML_VIEWS": view,
            "CALENDAR_TEMPLATE_DIR": str(template_dir),
            "CALENDAR_TIMEZONE": "UTC",
            "MANAGED_TAG": managed_tag,
            "MANAGED_FOLDER_ID": "",
            "DRY_RUN": "false",
            "DELETE_REMOTE_NOT_LOCAL": "false",
            "CREATE_LAYOUT_PER_UPLOAD": "true" if create_layout else "false",
            "ASSIGN_LAYOUT_ON_CHANGE": "true" if create_layout else "false",
            "PUBLISH_ON_CHANGE": "true",
            "IMMEDIATE_SHOW_ON_CHANGE": "true" if create_layout else "false",
            "DISPLAY_GROUP_ID": DISPLAY_GROUP_ID if create_layout else "",
            "TRIGGER_COLLECTNOW_ON_CHANGES": "false",
            "LOG_FILE": "",
        }
    )
    _write_env(isolated_scripts / ".env", scenario_env)

    assert not api.media_by_tag(managed_tag), f"Pre-existing media uses test tag {managed_tag}"
    assert not api.layouts_by_tag(managed_tag), f"Pre-existing layout uses test tag {managed_tag}"
    assert not api.layouts_by_name(legacy_layout_name), "Legacy sentinel layout already exists"

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

    pngs = list(media_dir.glob(f"calendar_{view}_*.png"))
    assert len(pngs) == 1, f"Expected one generated PNG, found {pngs}"
    assert _png_dimensions(pngs[0]) == (1920, 1080)
    assert not list(root.rglob("*.htz")), "Calendar command produced an HTZ package"

    media = api.media_by_tag(managed_tag)
    assert len(media) == 1, f"Expected one tagged media item, found {len(media)}"
    media_item = media[0]
    media_id = str(media_item.get("mediaId") or media_item.get("id") or "")
    start_date = pngs[0].stem.rsplit("_", 1)[1]
    expected_tags = {managed_tag, "calendar-html", "calendar-image", f"calendar-{view}", start_date}
    assert expected_tags <= _tags(media_item), f"Media tags missing: {expected_tags - _tags(media_item)}"
    assert str(media_item.get("name") or media_item.get("fileName") or "").endswith(".png")
    assert not api.layouts_by_name(legacy_layout_name), "Legacy reusable calendar layout was created"

    ownership_tag = f"xibo-sync-media:{media_id}"
    layouts = api.layouts_by_tag(ownership_tag)
    if create_layout:
        assert len(layouts) == 1, f"Expected one ownership-tagged layout, found {len(layouts)}"
        layout = layouts[0]
        layout_id = str(layout.get("layoutId") or layout.get("id") or "")
        assert managed_tag in _tags(layout)
        assert str(layout.get("publishedStatusId")) == "1", f"Layout is not published: {layout}"
        assert _contains_id(api.media_layout_usage(media_id), layout_id), (
            f"Media usage does not reference layoutId={layout_id}"
        )
        assert "Assigning layouts" in output and "Sending changeLayout" in output
    else:
        assert not layouts, "CREATE_LAYOUT_PER_UPLOAD=false created an ownership-tagged layout"
        assert not api.layouts_by_tag(managed_tag), "CREATE_LAYOUT_PER_UPLOAD=false mutated layouts"
        layout = None

    return {
        "mode": mode,
        "media_id": media_id,
        "layout_id": str(layout.get("layoutId") or layout.get("id")) if layout else None,
        "managed_tag": managed_tag,
        "output": output,
    }


def main() -> int:
    _selected_path, selected_env = _load_selected_env()
    production_env = SCRIPTS_DIR / ".env"
    production_before = production_env.read_bytes() if production_env.exists() else None
    api = CmsApi(selected_env)
    api.request("GET", "/library", params={"start": 0, "length": 1})
    token = uuid.uuid4().hex[:10]
    managed_tags = [
        f"system-test-calendar-enabled-{token}",
        f"system-test-calendar-disabled-{token}",
    ]
    cleanup_errors: list[str] = []

    try:
        with tempfile.TemporaryDirectory(prefix="xibo-calendar-system-") as temp_dir:
            root = Path(temp_dir)
            _run_scenario(api, selected_env, root, token, create_layout=True)
            _run_scenario(api, selected_env, root, token, create_layout=False)
    finally:
        for managed_tag in reversed(managed_tags):
            try:
                layouts = api.layouts_by_tag(managed_tag)
                for layout in layouts:
                    layout_id = str(layout.get("layoutId") or layout.get("id") or "")
                    api.unassign_layout(layout_id)
                    api.delete_layout(layout_id)
                for media in api.media_by_tag(managed_tag):
                    api.delete_media(str(media.get("mediaId") or media.get("id") or ""))
                assert not api.media_by_tag(managed_tag)
                assert not api.layouts_by_tag(managed_tag)
            except Exception as exc:
                cleanup_errors.append(f"{managed_tag}: {exc}")
        production_after = production_env.read_bytes() if production_env.exists() else None
        if production_after != production_before:
            cleanup_errors.append("scripts/.env changed during isolated system testing")

    if cleanup_errors:
        raise RuntimeError("Cleanup verification failed: " + "; ".join(cleanup_errors))
    print(f"SYSTEM TEST PASS: token={token}; enabled and disabled scenarios verified; cleanup passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())