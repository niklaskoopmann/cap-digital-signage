"""Minimal .env read/write helpers that preserve comments where possible."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional
import io
import json
import os
import re
import stat
import tempfile
from collections.abc import Mapping

from dotenv.parser import parse_stream


CREDENTIAL_KEYS = frozenset({
    "CMS_CLIENT_ID", "CMS_CLIENT_SECRET", "GRAPH_TENANT_ID",
    "GRAPH_CLIENT_ID", "GRAPH_CLIENT_SECRET",
})
CMS_CREDENTIAL_KEYS = frozenset({"CMS_CLIENT_ID", "CMS_CLIENT_SECRET"})
SETTING_KEYS = frozenset("""
CMS_BASE_URL CMS_VERIFY_TLS CMS_TIMEOUT_SECONDS AUTH_MODE SHAREPOINT_MEDIA_DIR
CALENDAR_IMAGE_DIR SYNC_MODE GRAPH_SHAREPOINT_SITE_ID GRAPH_SHAREPOINT_FOLDER
GRAPH_CALENDAR_USER_ID GRAPH_CALENDAR_DAYS_AHEAD GRAPH_CALENDAR_KEEP_SNAPSHOTS
GRAPH_TIMEOUT_SECONDS GRAPH_BASE_URL CALENDAR_JSON_PATH CALENDAR_DATASET_NAME
CALENDAR_DATASET_CODE CALENDAR_UPLOAD_CANCELLED_EVENTS MEDIA_EXTENSIONS COMPARE_MODE
MANAGED_TAG DELETE_LAYOUT_WITH_MEDIA ONLY_DELETE_MANAGED_TAG MANAGED_FOLDER_ID
UPLOAD_NEW_LOCAL DELETE_REMOTE_NOT_LOCAL DRY_RUN DISPLAY_GROUP_ID
TRIGGER_COLLECTNOW_ON_CHANGES CREATE_LAYOUT_PER_UPLOAD ASSIGN_LAYOUT_ON_CHANGE
PUBLISH_ON_CHANGE IMMEDIATE_SHOW_ON_CHANGE XIBO_UPLOAD_FIELD HASH_TAG_PREFIX
LOG_LEVEL LOG_FILE CALENDAR_ENABLE_HTML CALENDAR_HTML_VIEWS CALENDAR_TEMPLATE_DIR
CALENDAR_AUTO_PUBLISH CALENDAR_TIMEZONE CALENDAR_PACKAGE_RETENTION_DAYS
CALENDAR_EVENT_RETENTION_DAYS CALENDAR_RENDER_CRON CALENDAR_RENDER_RUN_ON_START
CALENDAR_RENDER_OUTPUT_DIR CALENDAR_CLEANUP_OLD_VIEW_UPLOADS TZ
""".split())


def is_dry_run(values: Mapping[str, str]) -> bool:
    return values.get("DRY_RUN", "false").strip().lower() in ("1", "true", "yes", "y", "on")


def dotenv_assignments(path: Path):
    try:
        if path.exists():
            with path.open(encoding="utf-8", newline="") as stream:
                text = stream.read()
        else:
            text = ""
    except (OSError, UnicodeError):
        raise ValueError(f"Cannot read configuration: {path}") from None
    bindings = list(parse_stream(io.StringIO(text)))
    if any(binding.error or (binding.key is not None and binding.value is None) for binding in bindings):
        raise ValueError(f"Invalid dotenv assignment in {path}")
    values: dict[str, str] = {}
    for binding in bindings:
        if binding.key is not None:
            value = binding.value or ""
            if binding.key in values and values[binding.key] != value:
                raise ValueError(f"Conflicting duplicate key {binding.key} in {path}")
            values[binding.key] = value
    return values, bindings


def validate_values(values: object, allowed: frozenset[str], path: Path) -> dict[str, str]:
    if not isinstance(values, dict) or any(
        not isinstance(key, str) or key not in allowed or not isinstance(value, str)
        for key, value in values.items()
    ):
        raise ValueError(f"Invalid keys or string values in {path}")
    return values


def read_json_file(path: Path, allowed: frozenset[str]) -> dict[str, str]:
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    try:
        if not path.exists():
            return {}
        values = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)
    except (OSError, UnicodeError, ValueError):
        raise ValueError(f"Cannot read valid JSON configuration: {path}") from None
    return validate_values(values, allowed, path)


def atomic_write(path: Path, text: str) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        permissions = stat.S_IMODE(path.stat().st_mode) & 0o600 if path.exists() else 0o600
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            if os.name != "nt":
                os.fchmod(stream.fileno(), permissions)
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError:
        raise ValueError(f"Cannot save configuration: {path}; retry after checking access") from None
    finally:
        if temporary is not None and os.path.exists(temporary):
            os.unlink(temporary)


def write_json_file(path: Path, values: dict[str, str], allowed: frozenset[str]) -> None:
    validate_values(values, allowed, path)
    atomic_write(path, json.dumps(values, indent=2, sort_keys=True) + "\n")


def load_settings(directory: Path, *, service: bool = False,
                  environ: Mapping[str, str] | None = None) -> tuple[dict[str, str], dict[str, str]]:
    baseline, _ = dotenv_assignments(directory / ".env")
    legacy = CREDENTIAL_KEYS.intersection(baseline)
    if legacy:
        target = "calendar-service" if service else "sync"
        raise ValueError(f"Credentials in {directory / '.env'}: {', '.join(sorted(legacy))}; run --migrate-config {target}")
    validate_values(baseline, SETTING_KEYS, directory / ".env")
    state = {} if service else read_json_file(directory / "sync-state.json", SETTING_KEYS)
    credentials = read_json_file(directory / "secrets.json", CMS_CREDENTIAL_KEYS if service else CREDENTIAL_KEYS)
    effective: dict[str, str] = {}
    sources: dict[str, str] = {}
    environment = os.environ if environ is None else environ
    for label, values in ((".env", baseline), ("saved state", state), ("secrets.json", credentials),
                          ("environment", environment)):
        for key, value in values.items():
            if key in SETTING_KEYS or key in (CMS_CREDENTIAL_KEYS if service else CREDENTIAL_KEYS):
                effective[key] = value
                sources[key] = label
    return effective, sources


def save_edits(directory: Path, before: Mapping[str, str], after: Mapping[str, str], *, dry_run: bool = False) -> None:
    state_path = directory / "sync-state.json"
    secret_path = directory / "secrets.json"
    state = read_json_file(state_path, SETTING_KEYS)
    secrets = read_json_file(secret_path, CREDENTIAL_KEYS)
    state_changed = secret_changed = False
    for key, value in after.items():
        if value != before.get(key, ""):
            if key in CREDENTIAL_KEYS:
                secrets[key] = value
                secret_changed = True
            else:
                state[key] = value
                state_changed = True
    validate_values(state, SETTING_KEYS, state_path)
    validate_values(secrets, CREDENTIAL_KEYS, secret_path)
    if dry_run:
        return
    try:
        if state_changed:
            write_json_file(state_path, state, SETTING_KEYS)
        if secret_changed:
            write_json_file(secret_path, secrets, CREDENTIAL_KEYS)
    except ValueError:
        raise ValueError("Settings save failed; some fields may already be saved. Check file access and retry.") from None


def assignment_comments(text: str) -> str:
    prefix_match = re.match(r"(?:[ \t]*\r?\n)*", text)
    prefix = prefix_match.group()
    assignment = text[prefix_match.end():]
    line_end = min((index for index in (assignment.find("\r"), assignment.find("\n")) if index >= 0), default=len(assignment))
    line = assignment[:line_end]
    line_ending = assignment[line_end:]
    assignment_match = re.match(r"[ \t]*(?:export[ \t]+)?(?:[^=\s#]+|'[^']+')\s*=[ \t]*", line)
    if assignment_match is None:
        return prefix

    value_start = assignment_match.end()
    quote = line[value_start] if value_start < len(line) and line[value_start] in "'\"" else None
    escaped = False
    value_content_start = value_start + 1 if quote is not None else value_start
    for index in range(value_content_start, len(line)):
        char = line[index]
        if quote is not None:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "#" and index > value_start and line[index - 1] in " \t":
            return prefix + line[index:] + line_ending
    return prefix


def migrate_config(root: Path, target: str, *, dry_run: bool = False) -> None:
    prepared = []
    for name, directory, service in (
        ("sync", root / "scripts", False),
        ("calendar-service", root / "services" / "calendar_render_service", True),
    ):
        if target not in (name, "all"):
            continue
        source = directory / ".env"
        values, bindings = dotenv_assignments(source)
        keys = CMS_CREDENTIAL_KEYS if service else CREDENTIAL_KEYS
        secrets = read_json_file(directory / "secrets.json", keys)
        state = {} if service else read_json_file(directory / "sync-state.json", SETTING_KEYS)
        dry_run = dry_run or is_dry_run({**values, **state, **os.environ})
        moved = set()
        for key in keys | (frozenset() if service else {"DELETE_REMOTE_NOT_LOCAL"}):
            if key not in values:
                continue
            destination = secrets if key in keys else state
            if key in destination and destination[key] != values[key]:
                raise ValueError(f"Migration conflict for {key} in {directory}")
            destination[key] = values[key]
            moved.add(key)
        cleaned = "".join(
            assignment_comments(binding.original.string)
            if binding.key in moved else binding.original.string
            for binding in bindings
        )
        prepared.append((directory, source, keys, secrets, state, moved, cleaned))
    dry_run = dry_run or is_dry_run(os.environ)
    if dry_run:
        return
    for directory, source, keys, secrets, state, moved, cleaned in prepared:
        if not moved:
            continue
        if moved & keys:
            destination = directory / "secrets.json"
            write_json_file(destination, secrets, keys)
            if read_json_file(destination, keys) != secrets:
                raise ValueError(f"Migration verification failed: {destination}")
        if "DELETE_REMOTE_NOT_LOCAL" in moved:
            destination = directory / "sync-state.json"
            write_json_file(destination, state, SETTING_KEYS)
            if read_json_file(destination, SETTING_KEYS) != state:
                raise ValueError(f"Migration verification failed: {destination}")
        atomic_write(source, cleaned)


def _strip_inline_comment(value: str) -> str:
    """Strip inline comments of the form ``VALUE  # comment``.

    Args:
        value: Raw value text from the right-hand side of a ``KEY=VALUE`` pair.

    Returns:
        Value text with a trailing inline comment removed when present.
    """
    for i in range(len(value) - 1):
        if value[i].isspace() and value[i + 1] == "#":
            return value[:i].strip()
    return value.strip()


def read_env_file(env_path: Path) -> Dict[str, str]:
    """Read a simple ``KEY=VALUE`` .env file.

    Args:
        env_path: Path to the environment file.

    Returns:
        Mapping of parsed environment keys to values.
    """
    data: Dict[str, str] = {}
    if not env_path.exists():
        return data

    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        data[k.strip()] = _strip_inline_comment(v)
    return data


def write_env_file(env_path: Path, values: Dict[str, str], original_lines: Optional[List[str]] = None) -> None:
    """Write the .env file while preserving existing comments where possible.

    Args:
        env_path: Destination path for the environment file.
        values: Key/value pairs to write back to the file.
        original_lines: Optional original file contents used to preserve layout.
    """
    if original_lines is None and env_path.exists():
        original_lines = env_path.read_text(encoding="utf-8").splitlines()
    if original_lines is None:
        original_lines = []

    known_keys = set(values.keys())
    out_lines: List[str] = []
    seen: set[str] = set()

    for line in original_lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in known_keys:
                out_lines.append(f"{k}={values[k]}")
                seen.add(k)
            else:
                out_lines.append(line)
        else:
            out_lines.append(line)

    for k in sorted(known_keys):
        if k not in seen:
            out_lines.append(f"{k}={values[k]}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
