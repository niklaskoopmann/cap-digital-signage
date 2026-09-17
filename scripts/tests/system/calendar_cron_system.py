"""Live checks for the container-local calendar cron scheduler.

Run explicitly from ``scripts/`` with ``SYSTEM_TEST_ENV_FILE`` naming a test-only
local-CMS environment file. This module is excluded from the default pytest suite.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from urllib.parse import urlparse

import requests
from dotenv import dotenv_values


SCRIPTS_DIR = Path(__file__).resolve().parents[2]
CONTAINER = os.environ.get("SYSTEM_TEST_CALENDAR_CONTAINER", "xibo-docker-442-calendar-render-service-1")
EXPECTED_CRON = os.environ.get("SYSTEM_TEST_EXPECTED_CRON", "0 0 * * *")


def _load_env() -> dict[str, str]:
    selected = os.environ.get("SYSTEM_TEST_ENV_FILE", "").strip()
    if not selected:
        raise RuntimeError("SYSTEM_TEST_ENV_FILE must name a test-only environment file")
    path = Path(selected).expanduser().resolve()
    if path == (SCRIPTS_DIR / ".env").resolve():
        raise RuntimeError("SYSTEM_TEST_ENV_FILE must not point to scripts/.env")
    values = {key: value for key, value in dotenv_values(path).items() if value is not None}
    endpoint = values.get("CMS_BASE_URL", "").strip()
    parsed = urlparse(endpoint)
    if parsed.scheme != "http" or parsed.hostname not in {"localhost", "127.0.0.1"} or parsed.port not in {None, 80}:
        raise RuntimeError("The system-test CMS endpoint must be http://localhost")
    values["CMS_BASE_URL"] = endpoint.rstrip("/")
    return values


def _cms_probe(values: dict[str, str]) -> None:
    session = requests.Session()
    response = session.post(
        f"{values['CMS_BASE_URL']}/api/authorize/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": values.get("CMS_CLIENT_ID", ""),
            "client_secret": values.get("CMS_CLIENT_SECRET", ""),
        },
        timeout=int(values.get("CMS_TIMEOUT_SECONDS", "30")),
        verify=values.get("CMS_VERIFY_TLS", "false").lower() == "true",
    )
    response.raise_for_status()
    session.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    session.get(
        f"{values['CMS_BASE_URL']}/api/library",
        params={"start": 0, "length": 1},
        timeout=int(values.get("CMS_TIMEOUT_SECONDS", "30")),
        verify=False,
    ).raise_for_status()


def _cms_media_by_tag(values: dict[str, str], tag: str) -> list[dict]:
    session = requests.Session()
    response = session.post(
        f"{values['CMS_BASE_URL']}/api/authorize/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": values.get("CMS_CLIENT_ID", ""),
            "client_secret": values.get("CMS_CLIENT_SECRET", ""),
        },
        timeout=int(values.get("CMS_TIMEOUT_SECONDS", "30")),
        verify=values.get("CMS_VERIFY_TLS", "false").lower() == "true",
    )
    response.raise_for_status()
    session.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    response = session.get(
        f"{values['CMS_BASE_URL']}/api/library",
        params={"tags": tag, "exactTags": 1, "embed": "tags"},
        timeout=int(values.get("CMS_TIMEOUT_SECONDS", "30")),
        verify=values.get("CMS_VERIFY_TLS", "false").lower() == "true",
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("data", payload) if isinstance(payload, dict) else payload
    if isinstance(items, dict):
        items = items.get("rows", [items])
    return [item for item in items if isinstance(item, dict) and tag in _tags(item)]


def _cms_delete_media(values: dict[str, str], media_id: str) -> None:
    session = requests.Session()
    response = session.post(
        f"{values['CMS_BASE_URL']}/api/authorize/access_token",
        data={
            "grant_type": "client_credentials",
            "client_id": values.get("CMS_CLIENT_ID", ""),
            "client_secret": values.get("CMS_CLIENT_SECRET", ""),
        },
        timeout=int(values.get("CMS_TIMEOUT_SECONDS", "30")),
        verify=values.get("CMS_VERIFY_TLS", "false").lower() == "true",
    )
    response.raise_for_status()
    session.headers["Authorization"] = f"Bearer {response.json()['access_token']}"
    response = session.delete(
        f"{values['CMS_BASE_URL']}/api/library/{media_id}",
        data={"forceDelete": 1},
        timeout=int(values.get("CMS_TIMEOUT_SECONDS", "30")),
        verify=values.get("CMS_VERIFY_TLS", "false").lower() == "true",
    )
    response.raise_for_status()


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


def _docker_exec(*args: str) -> str:
    completed = subprocess.run(
        ["wsl", "docker", "exec", CONTAINER, *args],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout


def _docker_logs() -> str:
    completed = subprocess.run(
        ["wsl", "docker", "logs", CONTAINER],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return completed.stdout + completed.stderr


def _wait_for_crontab() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        completed = subprocess.run(
            ["wsl", "docker", "exec", CONTAINER, "sh", "-lc", "test -s /tmp/calendar-render-service.crontab"],
            check=False,
            capture_output=True,
        )
        if completed.returncode == 0:
            return
        time.sleep(1)
    raise AssertionError("Scheduler did not write its crontab")


def _wait_for_scheduled_cycle(values: dict[str, str], tag: str) -> None:
    deadline = time.monotonic() + int(os.environ.get("SYSTEM_TEST_TICK_TIMEOUT", "90"))
    while time.monotonic() < deadline:
        logs = _docker_logs()
        if logs.count("Calendar cycle complete") >= 2 and _cms_media_by_tag(values, tag):
            return
        time.sleep(5)
    raise AssertionError("No scheduled cycle completed while the scheduler was running")


def _wait_for_log(fragment: str) -> str:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        logs = _docker_logs()
        if fragment in logs:
            return logs
        time.sleep(1)
    raise AssertionError(f"Scheduler log did not contain {fragment!r}")


def main() -> int:
    values = _load_env()
    _cms_probe(values)
    inspect = subprocess.run(
        ["wsl", "docker", "inspect", CONTAINER, "--format", "{{json .Config.Cmd}}"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    command = json.loads(inspect.stdout)
    assert command == ["python", "-m", "calendar_render_service.scheduler"], command
    _wait_for_crontab()
    crontab = _docker_exec("sh", "-lc", "cat /tmp/calendar-render-service.crontab").strip()
    expected = os.environ.get("SYSTEM_TEST_EXPECTED_CRONTAB", "")
    if not expected:
        python_path = _docker_exec("sh", "-lc", "command -v python").strip()
        flock_path = _docker_exec("sh", "-lc", "command -v flock").strip()
        assert python_path.startswith("/"), python_path
        assert flock_path.startswith("/"), flock_path
        expected = f"{EXPECTED_CRON} {flock_path} -n /tmp/calendar-render-service.lock {python_path} -m calendar_render_service.service"
    assert crontab == expected, crontab
    timezone = _docker_exec("sh", "-lc", "date +%Z").strip()
    assert timezone in {"CET", "CEST"}, timezone
    logs = _wait_for_log("Calendar scheduler active:")
    assert "Calendar scheduler active:" in logs
    assert f"cron={EXPECTED_CRON}" in logs
    assert f"timezone={os.environ.get('SYSTEM_TEST_EXPECTED_TIMEZONE', 'Europe/Berlin')}" in logs

    tag = os.environ.get("SYSTEM_TEST_MANAGED_TAG", "").strip()
    if not tag:
        raise RuntimeError("SYSTEM_TEST_MANAGED_TAG must identify this run's unique media")
    for item in _cms_media_by_tag(values, tag):
        _cms_delete_media(values, str(item.get("mediaId") or item.get("id") or ""))
    assert not _cms_media_by_tag(values, tag), f"Could not clear test-owned media for {tag}"
    try:
        _wait_for_scheduled_cycle(values, tag)
        media = _cms_media_by_tag(values, tag)
        assert media, f"No CMS media found for test tag {tag}"

        overlap = subprocess.run(
            [
                "wsl", "docker", "exec", CONTAINER, "sh", "-lc",
                "flock -n /tmp/calendar-render-service.lock -c 'sleep 5' & "
                "sleep 0.2; flock -n /tmp/calendar-render-service.lock -c true",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert overlap.returncode != 0, "held scheduler lock did not block an overlapping cycle"
        print(f"SYSTEM TEST PASS: tag={tag}; media={len(media)}; scheduler, tick, timezone, and overlap verified")
    finally:
        for item in _cms_media_by_tag(values, tag):
            _cms_delete_media(values, str(item.get("mediaId") or item.get("id") or ""))
        assert not _cms_media_by_tag(values, tag), f"Test media cleanup failed for {tag}"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
