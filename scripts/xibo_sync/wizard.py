"""Interactive .env configuration wizard for the sync tool."""

from __future__ import annotations

from pathlib import Path

from .env_io import read_env_file, write_env_file
from .ui import prompt_edit, prompt_password, ui_header, ui_ok


def config_wizard(env_path: Path) -> None:
    """Prompt the operator for settings and persist them to ``scripts/.env``.

    Args:
        env_path: Path to the ``.env`` file that should be updated.
    """
    env_data = read_env_file(env_path)
    original_lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

    ui_header("Configuration Wizard", "Edit settings. Press ENTER to keep current values.")

    def must_be_url(v: str):
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("Must start with http:// or https://")

    def must_be_mode(v: str):
        if v.lower() not in ("filename", "hash"):
            raise ValueError("Allowed: filename | hash")

    def must_be_auth(v: str):
        if v.lower() not in ("none", "oauth"):
            raise ValueError("Allowed: none | oauth")

    def get(k: str, default: str) -> str:
        return env_data.get(k, default)

    env_data["CMS_BASE_URL"] = prompt_edit(
        "CMS_BASE_URL",
        get("CMS_BASE_URL", "http://192.168.1.1"),
        help_text="Xibo CMS base URL on the hotspot network.",
        validator=must_be_url,
    )
    env_data["CMS_VERIFY_TLS"] = prompt_edit(
        "CMS_VERIFY_TLS",
        get("CMS_VERIFY_TLS", "false"),
        help_text="Use true only for HTTPS with valid certs.",
    )
    env_data["CMS_TIMEOUT_SECONDS"] = prompt_edit(
        "CMS_TIMEOUT_SECONDS",
        get("CMS_TIMEOUT_SECONDS", "30"),
        help_text="HTTP timeout in seconds.",
        validator=lambda v: int(v),
    )

    env_data["AUTH_MODE"] = prompt_edit(
        "AUTH_MODE",
        get("AUTH_MODE", "oauth"),
        help_text="none (no auth header) | oauth (OAuth2 client_credentials)",
        validator=must_be_auth,
    )

    if env_data["AUTH_MODE"].strip().lower() == "oauth":
        env_data["CMS_CLIENT_ID"] = prompt_edit(
            "CMS_CLIENT_ID",
            get("CMS_CLIENT_ID", ""),
            help_text="OAuth Client ID from Xibo CMS (Administration > Applications).",
        )
        env_data["CMS_CLIENT_SECRET"] = prompt_password(
            "CMS_CLIENT_SECRET",
            get("CMS_CLIENT_SECRET", ""),
        )
    else:
        env_data.setdefault("CMS_CLIENT_ID", get("CMS_CLIENT_ID", ""))
        env_data.setdefault("CMS_CLIENT_SECRET", get("CMS_CLIENT_SECRET", ""))

    env_data["LOCAL_MEDIA_DIR"] = prompt_edit(
        "LOCAL_MEDIA_DIR",
        get("LOCAL_MEDIA_DIR", "../media"),
        help_text="Relative to scripts/ recommended (../media).",
    )
    env_data["CALENDAR_JSON_PATH"] = prompt_edit(
        "CALENDAR_JSON_PATH",
        get("CALENDAR_JSON_PATH", r"C:\Users\rogrun\OneDrive - Capgemini\Xibo_Sync\office_calendar"),
        help_text="Calendar snapshot directory or JSON file. Relative paths are resolved from scripts/.",
    )
    env_data["CALENDAR_DATASET_NAME"] = prompt_edit(
        "CALENDAR_DATASET_NAME",
        get("CALENDAR_DATASET_NAME", "office_calendar_events"),
    )
    env_data["CALENDAR_DATASET_CODE"] = prompt_edit(
        "CALENDAR_DATASET_CODE",
        get("CALENDAR_DATASET_CODE", ""),
        help_text="Optional stable Xibo DataSet code. Blank disables code filtering.",
    )
    env_data["CALENDAR_UPLOAD_CANCELLED_EVENTS"] = prompt_edit(
        "CALENDAR_UPLOAD_CANCELLED_EVENTS",
        get("CALENDAR_UPLOAD_CANCELLED_EVENTS", "false"),
        help_text="Include cancelled events in the replacement snapshot.",
    )

    env_data["CALENDAR_ENABLE_HTML"] = prompt_edit(
        "CALENDAR_ENABLE_HTML",
        get("CALENDAR_ENABLE_HTML", "false"),
        help_text="Generate HTML calendar packages for Xibo widgets.",
    )
    env_data["CALENDAR_HTML_VIEWS"] = prompt_edit(
        "CALENDAR_HTML_VIEWS",
        get("CALENDAR_HTML_VIEWS", "today,this_week,next_2_weeks"),
        help_text="Comma-separated view names (must have matching views/*.json in template dir).",
    )
    env_data["CALENDAR_TEMPLATE_DIR"] = prompt_edit(
        "CALENDAR_TEMPLATE_DIR",
        get("CALENDAR_TEMPLATE_DIR", "templates/calendar"),
        help_text="Template directory containing template.html and views/*.json.",
    )
    env_data["CALENDAR_AUTO_PUBLISH"] = prompt_edit(
        "CALENDAR_AUTO_PUBLISH",
        get("CALENDAR_AUTO_PUBLISH", "true"),
        help_text="Auto-publish layouts after each package upload.",
    )
    env_data["CALENDAR_TIMEZONE"] = prompt_edit(
        "CALENDAR_TIMEZONE",
        get("CALENDAR_TIMEZONE", "Europe/Berlin"),
        help_text="IANA timezone name for event filtering and display labels (e.g. Europe/Berlin, Europe/Amsterdam). Daylight saving transitions are handled automatically.",
    )
    env_data["CALENDAR_PACKAGE_RETENTION_DAYS"] = prompt_edit(
        "CALENDAR_PACKAGE_RETENTION_DAYS",
        get("CALENDAR_PACKAGE_RETENTION_DAYS", "30"),
        help_text="Delete local calendar packages older than this many days.",
        validator=lambda v: int(v),
    )
    env_data["MEDIA_EXTENSIONS"] = prompt_edit(
        "MEDIA_EXTENSIONS",
        get("MEDIA_EXTENSIONS", ".jpg,.jpeg,.png,.gif,.mp4,.mov,.mkv,.webm"),
        help_text="Comma-separated file extensions to sync.",
    )
    env_data["COMPARE_MODE"] = prompt_edit(
        "COMPARE_MODE",
        get("COMPARE_MODE", "filename"),
        help_text="filename | hash",
        validator=must_be_mode,
    )

    env_data["MANAGED_TAG"] = prompt_edit(
        "MANAGED_TAG",
        get("MANAGED_TAG", "xibo-sync"),
        help_text="Tag used for media managed by this script.",
    )
    env_data["ONLY_DELETE_MANAGED_TAG"] = prompt_edit(
        "ONLY_DELETE_MANAGED_TAG",
        get("ONLY_DELETE_MANAGED_TAG", "true"),
        help_text="Recommended true to avoid deleting unrelated media.",
    )
    env_data["MANAGED_FOLDER_ID"] = prompt_edit(
        "MANAGED_FOLDER_ID",
        get("MANAGED_FOLDER_ID", ""),
        help_text="Optional folderId restriction. Blank = no restriction.",
    )

    env_data["UPLOAD_NEW_LOCAL"] = prompt_edit("UPLOAD_NEW_LOCAL", get("UPLOAD_NEW_LOCAL", "true"))
    env_data["DELETE_REMOTE_NOT_LOCAL"] = prompt_edit(
        "DELETE_REMOTE_NOT_LOCAL",
        get("DELETE_REMOTE_NOT_LOCAL", "false"),
        help_text="Default for deletion question per run.",
    )
    env_data["DRY_RUN"] = prompt_edit(
        "DRY_RUN",
        get("DRY_RUN", "false"),
        help_text="true = preview only (no upload/delete)",
    )

    env_data["DISPLAY_GROUP_ID"] = prompt_edit(
        "DISPLAY_GROUP_ID",
        get("DISPLAY_GROUP_ID", ""),
        help_text="Optional displayGroupId for Collect Now.",
    )
    env_data["TRIGGER_COLLECTNOW_ON_CHANGES"] = prompt_edit(
        "TRIGGER_COLLECTNOW_ON_CHANGES",
        get("TRIGGER_COLLECTNOW_ON_CHANGES", "true"),
    )

    env_data["XIBO_UPLOAD_FIELD"] = prompt_edit(
        "XIBO_UPLOAD_FIELD",
        get("XIBO_UPLOAD_FIELD", "files"),
        help_text="Multipart field name; script tries fallbacks too.",
    )
    env_data["HASH_TAG_PREFIX"] = prompt_edit(
        "HASH_TAG_PREFIX",
        get("HASH_TAG_PREFIX", "sha256:"),
    )

    env_data["LOG_LEVEL"] = prompt_edit("LOG_LEVEL", get("LOG_LEVEL", "INFO"))
    env_data["LOG_FILE"] = prompt_edit(
        "LOG_FILE",
        get("LOG_FILE", "logs/sync_xibo.log"),
        help_text="Optional log file path (relative). Blank disables file logging.",
    )

    write_env_file(env_path, env_data, original_lines=original_lines)
    ui_ok("Saved updated settings to .env")
