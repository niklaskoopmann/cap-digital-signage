from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import logging
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
from requests_toolbelt.multipart.encoder import MultipartEncoder, MultipartEncoderMonitor

# Pretty CLI UI
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich import box
from rich.text import Text
from rich.progress import (
    Progress,
    BarColumn,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

console = Console()


# =============================================================================
# .env read/write helpers
# =============================================================================

def _strip_inline_comment(value: str) -> str:
    """Strip inline comments of the form:  VALUE  # comment

    Notes:
    - Only strips when a '#' is preceded by whitespace.
    - Allows passwords/tokens containing '#', as long as you don't put a space before '#'.
      Example: WIFI_PASSWORD=abc#123  (kept)
               WIFI_PASSWORD=abc #123 (comment stripped)
    """
    # Find first occurrence of whitespace + '#'
    for i in range(len(value) - 1):
        if value[i].isspace() and value[i + 1] == '#':
            return value[:i].strip()
    return value.strip()


def read_env_file(env_path: Path) -> Dict[str, str]:
    """Read a simple KEY=VALUE .env file (ignores comments/blank lines).

    Supports inline comments after values when preceded by whitespace.
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
    """Write the .env file while preserving existing comments/unknown keys when possible.

    Only keys present in `values` are updated/added.
    """
    if original_lines is None and env_path.exists():
        original_lines = env_path.read_text(encoding="utf-8").splitlines()
    if original_lines is None:
        original_lines = []

    known_keys = set(values.keys())
    out_lines: List[str] = []
    seen: set[str] = set()

    # Preserve original ordering and comments; overwrite only known keys
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

    # Append any new keys not seen yet
    for k in sorted(known_keys):
        if k not in seen:
            out_lines.append(f"{k}={values[k]}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")


# =============================================================================
# Rich UI helpers
# =============================================================================

def ui_header(title: str, subtitle: Optional[str] = None) -> None:
    """Show a nice header panel."""
    txt = Text()
    txt.append(title, style="bold white")
    if subtitle:
        txt.append("\n")
        txt.append(subtitle, style="dim")
    console.print(Panel(txt, box=box.ROUNDED, border_style="cyan"))


def ui_error(msg: str) -> None:
    console.print(Panel(Text(msg, style="bold red"), box=box.ROUNDED, border_style="red"))


def ui_info(msg: str) -> None:
    console.print(f"[cyan]ℹ[/cyan] {msg}")


def ui_ok(msg: str) -> None:
    console.print(f"[green]✅[/green] {msg}")


def ui_warn(msg: str) -> None:
    console.print(f"[yellow]⚠[/yellow] {msg}")


def ui_settings_table(env_data: Dict[str, str]) -> None:
    """Show a summary table of relevant settings (secrets masked)."""
    keys = [
        "WIFI_SSID", "WIFI_PROFILE", "WIFI_AUTH", "WIFI_CIPHER", "WIFI_CONNECT_TIMEOUT_SECONDS",
        "CMS_BASE_URL", "CMS_VERIFY_TLS", "CMS_TIMEOUT_SECONDS",
        "AUTH_MODE", "CMS_CLIENT_ID", "CMS_CLIENT_SECRET",
        "LOCAL_MEDIA_DIR", "MEDIA_EXTENSIONS", "COMPARE_MODE",
        "MANAGED_TAG", "ONLY_DELETE_MANAGED_TAG", "MANAGED_FOLDER_ID",
        "UPLOAD_NEW_LOCAL", "DELETE_REMOTE_NOT_LOCAL", "DRY_RUN",
        "DISPLAY_GROUP_ID", "TRIGGER_COLLECTNOW_ON_CHANGES",
        "XIBO_UPLOAD_FIELD", "HASH_TAG_PREFIX",
        "LOG_LEVEL", "LOG_FILE",
    ]

    table = Table(
        title="Current Settings (from .env)",
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Key", style="white", no_wrap=True)
    table.add_column("Value", style="green")

    for k in keys:
        v = env_data.get(k, "")
        if k in ("WIFI_PASSWORD", "CMS_CLIENT_SECRET"):
            v = "******" if v else ""
        table.add_row(k, v)

    console.print(table)


def prompt_edit(key: str, current: str, help_text: str = "", validator=None) -> str:
    """Rich prompt for a setting; ENTER keeps current."""
    if help_text:
        console.print(f"[dim]{help_text}[/dim]")
    while True:
        val = Prompt.ask(f"[bold]{key}[/bold]", default=current)
        if validator:
            try:
                validator(val)
            except Exception as e:
                ui_error(f"Invalid value for {key}: {e}")
                continue
        return val


def prompt_password(key: str, current: str) -> str:
    """Masked password input. ENTER keeps current."""
    shown = "******" if current else ""
    console.print(f"[dim]{key} (press ENTER to keep current: {shown})[/dim]")
    val = getpass.getpass(f"{key}: ").strip()
    return current if val == "" else val


# =============================================================================
# Config
# =============================================================================

@dataclass
class Config:
    # Wi-Fi
    wifi_ssid: str
    wifi_profile: str
    wifi_password: str
    wifi_auth: str
    wifi_cipher: str
    wifi_timeout: int

    # Xibo
    cms_base_url: str
    cms_verify_tls: bool
    cms_timeout: int

    # Auth
    auth_mode: str                 # none | oauth
    cms_client_id: Optional[str]
    cms_client_secret: Optional[str]

    # Local media
    local_media_dir: Path
    media_extensions: Tuple[str, ...]
    compare_mode: str              # filename | hash

    # Safety scope
    managed_tag: str
    only_delete_managed_tag: bool
    managed_folder_id: Optional[str]

    # Behavior
    upload_new_local: bool
    delete_remote_not_local: bool
    dry_run: bool

    # Player refresh
    display_group_id: Optional[str]
    trigger_collectnow_on_changes: bool

    # Upload + hash tagging
    xibo_upload_field: str
    hash_tag_prefix: str

    # Logging
    log_level: str
    log_file: Optional[str]


def getenv_bool(name: str, default: bool) -> bool:
    v = os.getenv(name, str(default)).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def getenv_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)).strip())


def load_config() -> Config:
    """Load configuration from environment variables (after .env is loaded)."""
    wifi_ssid = os.getenv("WIFI_SSID", "").strip()
    if not wifi_ssid:
        raise ValueError("WIFI_SSID must be set")

    wifi_profile = os.getenv("WIFI_PROFILE", wifi_ssid).strip() or wifi_ssid
    wifi_password = os.getenv("WIFI_PASSWORD", "").strip()
    wifi_auth = os.getenv("WIFI_AUTH", "WPA2PSK").strip()
    wifi_cipher = os.getenv("WIFI_CIPHER", "AES").strip()

    # If network is not open, we need a password
    if wifi_auth.lower() != "open" and not wifi_password:
        raise ValueError("WIFI_PASSWORD must be set unless WIFI_AUTH=open")

    cms_base_url = os.getenv("CMS_BASE_URL", "").strip().rstrip("/")
    if not cms_base_url:
        raise ValueError("CMS_BASE_URL must be set (e.g. http://192.168.1.1)")

    auth_mode = os.getenv("AUTH_MODE", "none").strip().lower()
    if auth_mode not in ("none", "oauth"):
        raise ValueError("AUTH_MODE must be 'none' or 'oauth'")

    cms_client_id = os.getenv("CMS_CLIENT_ID", "").strip() or None
    cms_client_secret = os.getenv("CMS_CLIENT_SECRET", "").strip() or None
    if auth_mode == "oauth" and (not cms_client_id or not cms_client_secret):
        raise ValueError("For AUTH_MODE=oauth you must set CMS_CLIENT_ID and CMS_CLIENT_SECRET in .env")

    local_dir = os.getenv("LOCAL_MEDIA_DIR", "").strip()
    if not local_dir:
        raise ValueError("LOCAL_MEDIA_DIR must be set")

    exts = os.getenv("MEDIA_EXTENSIONS", ".jpg,.jpeg,.png,.gif,.mp4").strip()
    media_extensions = tuple(e.strip().lower() for e in exts.split(",") if e.strip())

    compare_mode = os.getenv("COMPARE_MODE", "filename").strip().lower()
    if compare_mode not in ("filename", "hash"):
        raise ValueError("COMPARE_MODE must be 'filename' or 'hash'")

    return Config(
        wifi_ssid=wifi_ssid,
        wifi_profile=wifi_profile,
        wifi_password=wifi_password,
        wifi_auth=wifi_auth,
        wifi_cipher=wifi_cipher,
        wifi_timeout=getenv_int("WIFI_CONNECT_TIMEOUT_SECONDS", 30),

        cms_base_url=cms_base_url,
        cms_verify_tls=getenv_bool("CMS_VERIFY_TLS", False),
        cms_timeout=getenv_int("CMS_TIMEOUT_SECONDS", 30),

        auth_mode=auth_mode,
        cms_client_id=cms_client_id,
        cms_client_secret=cms_client_secret,

        local_media_dir=Path(local_dir),
        media_extensions=media_extensions,
        compare_mode=compare_mode,

        managed_tag=os.getenv("MANAGED_TAG", "xibo-sync").strip(),
        only_delete_managed_tag=getenv_bool("ONLY_DELETE_MANAGED_TAG", True),
        managed_folder_id=os.getenv("MANAGED_FOLDER_ID", "").strip() or None,

        upload_new_local=getenv_bool("UPLOAD_NEW_LOCAL", True),
        delete_remote_not_local=getenv_bool("DELETE_REMOTE_NOT_LOCAL", False),
        dry_run=getenv_bool("DRY_RUN", False),

        display_group_id=os.getenv("DISPLAY_GROUP_ID", "").strip() or None,
        trigger_collectnow_on_changes=getenv_bool("TRIGGER_COLLECTNOW_ON_CHANGES", True),

        xibo_upload_field=os.getenv("XIBO_UPLOAD_FIELD", "files").strip(),
        hash_tag_prefix=os.getenv("HASH_TAG_PREFIX", "sha256:").strip(),

        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        log_file=os.getenv("LOG_FILE", "").strip() or None,
    )


def setup_logging(level: str, log_file: Optional[str], scripts_dir: Path) -> None:
    """Configure logging to console + optional file."""
    handlers: List[logging.Handler] = [logging.StreamHandler()]

    if log_file:
        lf_path = (scripts_dir / log_file).resolve() if not Path(log_file).is_absolute() else Path(log_file)
        lf_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(lf_path, encoding="utf-8"))

    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(message)s",
        handlers=handlers,
    )


# =============================================================================
# Wi-Fi management (Windows)
# =============================================================================

class WifiManager:
    """Connect/disconnect to Wi-Fi hotspot on Windows using netsh."""

    @staticmethod
    def _run_netsh(args: List[str]) -> subprocess.CompletedProcess:
        cmd = ["netsh"] + args
        logging.debug("Running: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, check=False)

    @staticmethod
    def current_ssid() -> Optional[str]:
        """Return the currently connected SSID, or None."""
        res = WifiManager._run_netsh(["wlan", "show", "interfaces"])
        if res.returncode != 0:
            return None

        for line in res.stdout.splitlines():
            if ":" in line:
                left, right = line.split(":", 1)
                if left.strip() == "SSID":
                    return right.strip() or None
        return None

    @staticmethod
    def profile_exists(profile_name: str) -> bool:
        """Check if a Wi-Fi profile exists in Windows."""
        res = WifiManager._run_netsh(["wlan", "show", "profiles"])
        if res.returncode != 0:
            return False
        return profile_name.lower() in res.stdout.lower()

    @staticmethod
    def _build_profile_xml(ssid: str, auth: str, cipher: str, password: str) -> str:
        """Build WLAN profile XML for WPA/WPA2 PSK or open networks."""
        # Minimal XML escaping
        ssid_xml = ssid.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        if auth.lower() == "open":
            security_block = """
            <security>
                <authEncryption>
                    <authentication>open</authentication>
                    <encryption>none</encryption>
                    <useOneX>false</useOneX>
                </authEncryption>
            </security>
            """
        else:
            security_block = f"""
            <security>
                <authEncryption>
                    <authentication>{auth}</authentication>
                    <encryption>{cipher}</encryption>
                    <useOneX>false</useOneX>
                </authEncryption>
                <sharedKey>
                    <keyType>passPhrase</keyType>
                    <protected>false</protected>
                    <keyMaterial>{password}</keyMaterial>
                </sharedKey>
            </security>
            """

        return f"""<?xml version=\"1.0\"?>
<WLANProfile xmlns=\"http://www.microsoft.com/networking/WLAN/profile/v1\">
    <name>{ssid_xml}</name>
    <SSIDConfig>
        <SSID>
            <name>{ssid_xml}</name>
        </SSID>
    </SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        {security_block}
    </MSM>
</WLANProfile>
"""

    @staticmethod
    def ensure_profile(ssid: str, profile_name: str, auth: str, cipher: str, password: str) -> None:
        """Ensure a WLAN profile exists. If not, import it from XML."""
        if WifiManager.profile_exists(profile_name):
            logging.info("Wi-Fi profile '%s' already exists.", profile_name)
            return

        logging.info("Wi-Fi profile '%s' not found. Creating/importing profile ...", profile_name)
        xml = WifiManager._build_profile_xml(ssid=ssid, auth=auth, cipher=cipher, password=password)

        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as f:
            xml_path = f.name
            f.write(xml)

        try:
            res = WifiManager._run_netsh(["wlan", "add", "profile", f"filename={xml_path}", "user=current"])
            if res.returncode != 0:
                raise RuntimeError(
                    "Failed to add Wi-Fi profile.\n"
                    f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
                )
            logging.info("Wi-Fi profile imported successfully.")
        finally:
            try:
                os.remove(xml_path)
            except Exception:
                pass

    @staticmethod
    def connect(ssid: str, profile: str, auth: str, cipher: str, password: str, timeout_seconds: int = 30) -> None:
        """Connect to Wi-Fi hotspot."""
        logging.info("Step 1: Connecting to Wi-Fi hotspot SSID='%s' ...", ssid)

        WifiManager.ensure_profile(ssid=ssid, profile_name=profile, auth=auth, cipher=cipher, password=password)

        res = WifiManager._run_netsh(["wlan", "connect", f"name={profile}"])
        if res.returncode != 0:
            raise RuntimeError(
                "Failed to start Wi-Fi connection.\n"
                f"STDOUT:\n{res.stdout}\nSTDERR:\n{res.stderr}"
            )

        # Wait until connected to correct SSID
        start = time.time()
        while time.time() - start < timeout_seconds:
            cur = WifiManager.current_ssid()
            if cur == ssid:
                logging.info("Connected to Wi-Fi SSID='%s'.", ssid)
                return
            time.sleep(1)

        raise TimeoutError(
            f"Timed out after {timeout_seconds}s waiting for Wi-Fi SSID='{ssid}'. "
            f"Current SSID='{WifiManager.current_ssid()}'"
        )

    @staticmethod
    def disconnect() -> None:
        """Disconnect from Wi-Fi."""
        logging.info("Final step: Disconnecting from Wi-Fi hotspot ...")
        res = WifiManager._run_netsh(["wlan", "disconnect"])
        if res.returncode != 0:
            logging.warning(
                "Wi-Fi disconnect returned %s.\nSTDOUT:\n%s\nSTDERR:\n%s",
                res.returncode,
                res.stdout,
                res.stderr,
            )
        else:
            logging.info("Disconnected from Wi-Fi.")


# =============================================================================
# Xibo API client (AUTH_MODE=oauth supported)
# =============================================================================

class XiboClient:
    """Minimal Xibo CMS API client (library sync)."""

    def __init__(self, base_url: str, verify_tls: bool, timeout: int):
        self.base_url = base_url.rstrip("/")
        self.verify_tls = verify_tls
        self.timeout = timeout
        self.session = requests.Session()
        self._oauth_client_id: Optional[str] = None
        self._oauth_client_secret: Optional[str] = None
        self._oauth_token_expires_at: Optional[float] = None

    def _api_url(self, path: str) -> str:
        return f"{self.base_url}/api{path}"

    def _token_url(self) -> str:
        # Standard Xibo endpoint for client_credentials
        return f"{self.base_url}/api/authorize/access_token"

    @staticmethod
    def _extract_data(json_obj):
        # Xibo frequently wraps responses in {"data": [...]} 
        if isinstance(json_obj, dict) and "data" in json_obj:
            return json_obj["data"]
        return json_obj

    def authenticate_oauth(self, client_id: str, client_secret: str) -> None:
        """OAuth2 client credentials flow.

        Stores token in session headers.
        """
        self._oauth_client_id = client_id
        self._oauth_client_secret = client_secret

        url = self._token_url()
        payload = {
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        }
        r = self.session.post(url, data=payload, timeout=self.timeout, verify=self.verify_tls)
        if r.status_code != 200:
            body_text = r.text
            try:
                body_json = r.json()
                body_text = str(body_json)
            except Exception:
                pass
            raise RuntimeError(f"OAuth token request failed ({r.status_code}): {body_text}")

        body = {}
        try:
            body = r.json()
        except Exception:
            pass

        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise RuntimeError(f"OAuth response missing access_token: {r.text}")

        expires_in = body.get("expires_in") if isinstance(body, dict) else None
        try:
            expires_val = float(expires_in) if expires_in is not None else 300.0
        except Exception:
            expires_val = 300.0

        self._oauth_token_expires_at = time.time() + expires_val - 60.0
        self.session.headers.update({"Authorization": f"Bearer {token}"})
        logging.info("Authenticated via OAuth.")

    def _ensure_token_valid(self) -> None:
        if ("Authorization" not in self.session.headers) and self._oauth_client_id and self._oauth_client_secret:
            logging.info("No OAuth token present - obtaining new token...")
            self.authenticate_oauth(self._oauth_client_id, self._oauth_client_secret)
            return

        if self._oauth_token_expires_at and time.time() >= self._oauth_token_expires_at:
            if self._oauth_client_id and self._oauth_client_secret:
                logging.info("OAuth token expired/near expiry - refreshing...")
                self.authenticate_oauth(self._oauth_client_id, self._oauth_client_secret)

    def _request(self, method: str, url: str, *, retry_on_401: bool = True, **kwargs):
        """Wrapper that refreshes OAuth token and retries once on 401."""
        self._ensure_token_valid()
        r = self.session.request(method, url, timeout=self.timeout, verify=self.verify_tls, **kwargs)
        if retry_on_401 and r.status_code in (401, 403) and self._oauth_client_id and self._oauth_client_secret:
            logging.warning("Request unauthorized (%s). Re-authenticating and retrying once...", r.status_code)
            self.authenticate_oauth(self._oauth_client_id, self._oauth_client_secret)
            r = self.session.request(method, url, timeout=self.timeout, verify=self.verify_tls, **kwargs)
        return r

    def health_check(self) -> None:
        """Simple call to verify API availability and auth status."""
        r = self._request(
            "GET",
            self._api_url("/library"),
            params={"start": 0, "length": 1},
        )
        if r.status_code in (401, 403):
            raise RuntimeError(
                f"API unauthorized ({r.status_code}). Set AUTH_MODE=oauth and provide CMS_CLIENT_ID/CMS_CLIENT_SECRET."
            )
        if r.status_code != 200:
            raise RuntimeError(f"API check failed ({r.status_code}): {r.text}")

    def list_library(self, managed_tag: Optional[str], folder_id: Optional[str]) -> List[dict]:
        """Fetch library items, optionally filtered by tag and/or folder."""
        logging.info("Step 3: Fetching CMS Library entries ...")

        items: List[dict] = []
        start = 0
        page_size = 1000

        while True:
            params = {"start": start, "length": page_size}
            if managed_tag:
                params["tags"] = managed_tag
            if folder_id:
                params["folderId"] = folder_id

            r = self._request("GET", self._api_url("/library"), params=params)
            if r.status_code != 200:
                raise RuntimeError(f"Library list failed ({r.status_code}): {r.text}")

            data = self._extract_data(r.json())
            if not isinstance(data, list):
                raise RuntimeError(f"Unexpected library response format: {r.text}")

            items.extend(data)

            if len(data) < page_size:
                break
            start += page_size

        logging.info("CMS Library fetched: %d item(s).", len(items))
        return items

    def tag_media(self, media_id: str, tag: str) -> None:
        """Add a tag to a media item."""
        r = self._request(
            "POST",
            self._api_url(f"/library/{media_id}/tag"),
            data={"tag": tag},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Tagging mediaId={media_id} failed ({r.status_code}): {r.text}")

    def upload_media(
        self,
        file_path: Path,
        name: Optional[str],
        folder_id: Optional[str],
        tags: List[str],
        preferred_field: str,
        dry_run: bool,
    ) -> Optional[dict]:
        """Upload media to Xibo with a rich progress bar."""
        logging.info("Uploading: %s", file_path.name)
        if dry_run:
            logging.info("[DRY_RUN] Would upload '%s'", file_path)
            return None

        url = self._api_url("/library")

        # Some Xibo versions use different multipart field names. We try candidates.
        field_candidates = [preferred_field]
        for cand in ("files", "file", "media", "upload"):
            if cand not in field_candidates:
                field_candidates.append(cand)

        last_error = None

        with Progress(
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=None),
            TextColumn("[green]{task.percentage:>3.0f}%"),
            TextColumn("•"),
            TransferSpeedColumn(),
            TextColumn("•"),
            TimeRemainingColumn(),
            console=console,
            transient=True,
        ) as progress:

            for upload_field in field_candidates:
                self._ensure_token_valid()
                f = open(file_path, "rb")
                try:
                    fields = {
                        upload_field: (file_path.name, f, "application/octet-stream"),
                    }
                    if name:
                        fields["name"] = name
                    if folder_id:
                        fields["folderId"] = folder_id

                    encoder = MultipartEncoder(fields=fields)
                    task_id = progress.add_task(f"Uploading {file_path.name}", total=encoder.len)

                    def _cb(monitor: MultipartEncoderMonitor):
                        progress.update(task_id, completed=monitor.bytes_read)

                    monitor = MultipartEncoderMonitor(encoder, _cb)
                    headers = {"Content-Type": monitor.content_type}

                    r = self._request(
                        "POST",
                        url,
                        data=monitor,
                        headers=headers,
                        retry_on_401=True,
                    )

                    if r.status_code in (200, 201):
                        payload = r.json()
                        data = self._extract_data(payload)
                        created = data[0] if isinstance(data, list) and data else data

                        if not created:
                            logging.warning("Upload succeeded but response has no media object: %s", payload)
                            return None

                        media_id = str(created.get("mediaId") or created.get("id") or "")
                        if media_id:
                            for t in tags:
                                self.tag_media(media_id, t)

                        return created

                    last_error = f"Upload attempt with field '{upload_field}' failed ({r.status_code}): {r.text}"
                    logging.warning(last_error)

                finally:
                    try:
                        f.close()
                    except Exception:
                        pass

        raise RuntimeError(last_error or "Upload failed (unknown reason)")

    def delete_media(self, media_id: str, dry_run: bool) -> None:
        """Delete a media item from the library."""
        logging.info("Deleting mediaId=%s ...", media_id)
        if dry_run:
            logging.info("[DRY_RUN] Would delete mediaId=%s", media_id)
            return

        r = self._request(
            "DELETE",
            self._api_url(f"/library/{media_id}"),
            data={"forceDelete": 1},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if r.status_code != 200:
            raise RuntimeError(f"Delete mediaId={media_id} failed ({r.status_code}): {r.text}")

    def collect_now(self, display_group_id: str, dry_run: bool) -> None:
        """Trigger Collect Now to refresh player content (optional)."""
        logging.info("Triggering Collect Now for displayGroupId=%s ...", display_group_id)
        if dry_run:
            logging.info("[DRY_RUN] Would call collectNow for displayGroupId=%s", display_group_id)
            return

        r = self._request(
            "POST",
            self._api_url(f"/displaygroup/{display_group_id}/action/collectNow"),
        )
        if r.status_code != 200:
            raise RuntimeError(f"collectNow failed ({r.status_code}): {r.text}")


# =============================================================================
# Diff helpers
# =============================================================================

def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Compute sha256 for a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def list_local_media(media_dir: Path, extensions: Tuple[str, ...]) -> List[Path]:
    """Find all files in media_dir matching extensions (recursive)."""
    if not media_dir.exists():
        raise FileNotFoundError(f"Local media directory does not exist: {media_dir.resolve()}")

    files: List[Path] = []
    for p in media_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in extensions:
            files.append(p)

    return sorted(files)


def build_local_index(files: List[Path], mode: str, hash_tag_prefix: str) -> Dict[str, Path]:
    """Map local keys -> file paths."""
    idx: Dict[str, Path] = {}
    if mode == "filename":
        for f in files:
            idx[f.name] = f
    else:
        for f in files:
            idx[f"{hash_tag_prefix}{sha256_file(f)}"] = f
    return idx


def build_remote_index(items: List[dict], mode: str, hash_tag_prefix: str) -> Dict[str, dict]:
    """Map remote keys -> item dict."""
    idx: Dict[str, dict] = {}
    for it in items:
        if mode == "filename":
            key = it.get("name") or it.get("fileName") or it.get("originalFileName")
            if key:
                idx[str(key)] = it
        else:
            tags = it.get("tags")
            found = None

            if isinstance(tags, str):
                for t in [x.strip() for x in tags.split(",")]:
                    if t.startswith(hash_tag_prefix):
                        found = t
                        break
            elif isinstance(tags, list):
                for t in tags:
                    if isinstance(t, str) and t.startswith(hash_tag_prefix):
                        found = t
                        break
                    if isinstance(t, dict):
                        tag_str = t.get("tag") or t.get("name")
                        if isinstance(tag_str, str) and tag_str.startswith(hash_tag_prefix):
                            found = tag_str
                            break

            if found:
                idx[found] = it

    return idx


# =============================================================================
# Config wizard (runs only if user chooses to override)
# =============================================================================

def config_wizard(env_path: Path) -> None:
    """Interactive editor; persists updated settings back to .env."""
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

    def must_be_wifi_auth(v: str):
        if v.lower() not in ("wpa2psk", "wpapsk", "open"):
            raise ValueError("Allowed: WPA2PSK | WPAPSK | open")

    def must_be_wifi_cipher(v: str):
        if v.upper() not in ("AES", "TKIP"):
            raise ValueError("Allowed: AES | TKIP")

    def get(k: str, default: str) -> str:
        return env_data.get(k, default)

    # Wi-Fi
    env_data["WIFI_SSID"] = prompt_edit("WIFI_SSID", get("WIFI_SSID", "XIBO-HOTSPOT"))
    env_data["WIFI_PROFILE"] = prompt_edit(
        "WIFI_PROFILE",
        get("WIFI_PROFILE", env_data["WIFI_SSID"]),
        help_text="Windows WLAN profile name (often same as SSID).",
    )
    env_data["WIFI_PASSWORD"] = prompt_password("WIFI_PASSWORD", get("WIFI_PASSWORD", ""))
    env_data["WIFI_AUTH"] = prompt_edit(
        "WIFI_AUTH",
        get("WIFI_AUTH", "WPA2PSK"),
        help_text="WPA2PSK | WPAPSK | open",
        validator=must_be_wifi_auth,
    )
    env_data["WIFI_CIPHER"] = prompt_edit(
        "WIFI_CIPHER",
        get("WIFI_CIPHER", "AES"),
        help_text="AES | TKIP",
        validator=must_be_wifi_cipher,
    )
    env_data["WIFI_CONNECT_TIMEOUT_SECONDS"] = prompt_edit(
        "WIFI_CONNECT_TIMEOUT_SECONDS",
        get("WIFI_CONNECT_TIMEOUT_SECONDS", "30"),
        help_text="Seconds to wait for Wi-Fi connection.",
        validator=lambda v: int(v),
    )

    # CMS
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

    # Auth
    env_data["AUTH_MODE"] = prompt_edit(
        "AUTH_MODE",
        get("AUTH_MODE", "none"),
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
        # Keep values if already set; do not delete.
        env_data.setdefault("CMS_CLIENT_ID", get("CMS_CLIENT_ID", ""))
        env_data.setdefault("CMS_CLIENT_SECRET", get("CMS_CLIENT_SECRET", ""))

    # Local media
    env_data["LOCAL_MEDIA_DIR"] = prompt_edit(
        "LOCAL_MEDIA_DIR",
        get("LOCAL_MEDIA_DIR", "../images"),
        help_text="Relative to scripts/ recommended (../images).",
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

    # Safety
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

    # Behavior
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

    # Refresh
    env_data["DISPLAY_GROUP_ID"] = prompt_edit(
        "DISPLAY_GROUP_ID",
        get("DISPLAY_GROUP_ID", ""),
        help_text="Optional displayGroupId for Collect Now.",
    )
    env_data["TRIGGER_COLLECTNOW_ON_CHANGES"] = prompt_edit(
        "TRIGGER_COLLECTNOW_ON_CHANGES",
        get("TRIGGER_COLLECTNOW_ON_CHANGES", "true"),
    )

    # Upload / hash
    env_data["XIBO_UPLOAD_FIELD"] = prompt_edit(
        "XIBO_UPLOAD_FIELD",
        get("XIBO_UPLOAD_FIELD", "files"),
        help_text="Multipart field name; script tries fallbacks too.",
    )
    env_data["HASH_TAG_PREFIX"] = prompt_edit(
        "HASH_TAG_PREFIX",
        get("HASH_TAG_PREFIX", "sha256:"),
    )

    # Logging
    env_data["LOG_LEVEL"] = prompt_edit("LOG_LEVEL", get("LOG_LEVEL", "INFO"))
    env_data["LOG_FILE"] = prompt_edit(
        "LOG_FILE",
        get("LOG_FILE", "logs/sync_xibo.log"),
        help_text="Optional log file path (relative). Blank disables file logging.",
    )

    write_env_file(env_path, env_data, original_lines=original_lines)
    ui_ok("Saved updated settings to .env")


# =============================================================================
# Main program
# =============================================================================

def main() -> int:
    scripts_dir = Path(__file__).resolve().parent
    env_path = scripts_dir / ".env"

    parser = argparse.ArgumentParser(description="Sync local media to Xibo CMS (with Wi-Fi connect)")
    parser.add_argument("--config", action="store_true", help="Run configuration wizard and exit")
    parser.add_argument("--yes", "-y", action="store_true", help="Non-interactive: accept defaults and skip prompts")
    parser.add_argument("--dry-run", action="store_true", help="Preview actions without uploading/deleting")
    parser.add_argument("--delete", dest="delete", action="store_true", help="Delete remote-only media without prompt")
    parser.add_argument("--no-delete", dest="delete", action="store_false", help="Do not delete remote-only media")
    parser.set_defaults(delete=None)
    args = parser.parse_args()

    ui_header("Xibo Media Sync", "Sync local images/videos to Xibo CMS via Wi-Fi hotspot")

    # Show current .env settings if present
    env_data = read_env_file(env_path)
    if env_data:
        ui_settings_table(env_data)
    else:
        ui_warn("No .env found yet. You should run the configuration wizard now.")

    console.print()
    if args.config:
        config_wizard(env_path)
        return 0

    override = False if args.yes else Confirm.ask(
        "[bold]Do you want to override/edit configuration values now?[/bold]",
        default=(not env_data),
    )
    if override:
        config_wizard(env_path)
        env_data = read_env_file(env_path)

    # Load config
    load_dotenv(env_path)
    cfg = load_config()

    # Setup logging
    setup_logging(cfg.log_level, cfg.log_file, scripts_dir)

    # Resolve local media dir relative to scripts/
    if not cfg.local_media_dir.is_absolute():
        cfg.local_media_dir = (scripts_dir / cfg.local_media_dir).resolve()

    # Run option: delete remote?
    console.print()
    ui_header("Run Options", "Choose whether remote-only media should be deleted this run")
    if args.delete is not None:
        run_delete = bool(args.delete)
    elif args.yes:
        run_delete = cfg.delete_remote_not_local
    else:
        run_delete = Confirm.ask(
            "Delete media from Xibo that is NOT present locally?",
            default=cfg.delete_remote_not_local,
        )

    # Always persist delete choice to .env
    env_data2 = read_env_file(env_path)
    env_data2["DELETE_REMOTE_NOT_LOCAL"] = "true" if run_delete else "false"
    write_env_file(env_path, env_data2)
    ui_ok("Saved deletion choice to .env (DELETE_REMOTE_NOT_LOCAL)")

    if args.dry_run:
        cfg.dry_run = True

    ui_info(f"Local media dir: {cfg.local_media_dir}")
    ui_info(f"Compare mode: {cfg.compare_mode} | Auth mode: {cfg.auth_mode} | Dry run: {cfg.dry_run}")

    wifi_connected = False
    changes_made = False

    try:
        # 1) Connect to Wi-Fi hotspot
        ui_info("Step 1: Connecting to Wi-Fi hotspot ...")
        WifiManager.connect(
            ssid=cfg.wifi_ssid,
            profile=cfg.wifi_profile,
            auth=cfg.wifi_auth,
            cipher=cfg.wifi_cipher,
            password=cfg.wifi_password,
            timeout_seconds=cfg.wifi_timeout,
        )
        wifi_connected = True
        ui_ok(f"Connected to SSID '{cfg.wifi_ssid}'")

        # 2) Connect to Xibo API
        ui_info("Step 2: Checking Xibo API availability ...")
        xibo = XiboClient(cfg.cms_base_url, cfg.cms_verify_tls, cfg.cms_timeout)

        if cfg.auth_mode == "oauth":
            assert cfg.cms_client_id and cfg.cms_client_secret
            xibo.authenticate_oauth(cfg.cms_client_id, cfg.cms_client_secret)

        xibo.health_check()
        ui_ok("Xibo API reachable")

        # 3) Scan local
        ui_info("Step 3: Scanning local media files ...")
        local_files = list_local_media(cfg.local_media_dir, cfg.media_extensions)
        ui_ok(f"Found {len(local_files)} local media file(s)")
        local_index = build_local_index(local_files, cfg.compare_mode, cfg.hash_tag_prefix)

        # 4) Scan remote (filtered by MANAGED_TAG / folder for safety)
        ui_info("Step 4: Fetching Xibo CMS library ...")
        remote_items = xibo.list_library(cfg.managed_tag, cfg.managed_folder_id)
        remote_index = build_remote_index(remote_items, cfg.compare_mode, cfg.hash_tag_prefix)
        ui_ok(f"Fetched {len(remote_items)} remote library item(s) (filtered)")

        # 5) Diff
        ui_info("Step 5: Calculating diff ...")
        to_upload = sorted(set(local_index.keys()) - set(remote_index.keys()))
        to_delete = sorted(set(remote_index.keys()) - set(local_index.keys()))
        ui_ok(f"To upload: {len(to_upload)} | Remote-only: {len(to_delete)}")

        # 6) Upload new files
        if cfg.upload_new_local and to_upload:
            ui_info("Step 6: Uploading new media ...")
            for key in to_upload:
                f = local_index[key]
                tags = [cfg.managed_tag]
                if cfg.compare_mode == "hash":
                    tags.append(key)  # sha256:<hash>

                xibo.upload_media(
                    file_path=f,
                    name=f.name,
                    folder_id=cfg.managed_folder_id,
                    tags=tags,
                    preferred_field=cfg.xibo_upload_field,
                    dry_run=cfg.dry_run,
                )
                changes_made = True
            ui_ok("Uploads complete")
        else:
            ui_info("Step 6: Upload skipped (nothing to upload or UPLOAD_NEW_LOCAL=false)")

        # 7) Delete remote-only media (optional)
        if run_delete and to_delete:
            ui_info("Step 7: Deleting remote-only media ...")

            if cfg.only_delete_managed_tag:
                ui_warn(f"Safety ON: Only deleting items with MANAGED_TAG='{cfg.managed_tag}'")

            for key in to_delete:
                it = remote_index[key]
                media_id = str(it.get("mediaId") or it.get("id") or "")
                if not media_id:
                    logging.warning("Skipping delete (no mediaId): %s", it)
                    continue

                # Safety: only delete items that contain the managed tag
                if cfg.only_delete_managed_tag:
                    tags = it.get("tags", "")
                    tags_str = ""
                    if isinstance(tags, str):
                        tags_str = tags
                    elif isinstance(tags, list):
                        tags_str = ",".join([t.get("tag", "") if isinstance(t, dict) else str(t) for t in tags])

                    if cfg.managed_tag not in tags_str:
                        ui_warn(f"Skipping delete mediaId={media_id} (missing managed tag)")
                        continue

                xibo.delete_media(media_id, dry_run=cfg.dry_run)
                changes_made = True

            ui_ok("Deletion complete")
        else:
            ui_info("Step 7: Delete skipped (disabled or none to delete)")

        # 8) Trigger refresh if configured
        if cfg.trigger_collectnow_on_changes and changes_made and cfg.display_group_id:
            ui_info("Step 8: Triggering player refresh (Collect Now) ...")
            xibo.collect_now(cfg.display_group_id, dry_run=cfg.dry_run)
            ui_ok("Collect Now triggered")
        else:
            ui_info("Step 8: Refresh skipped (no changes, DISPLAY_GROUP_ID empty, or disabled)")

        ui_ok("SUCCESS: Sync completed")
        return 0

    except Exception as e:
        logging.exception("FAILED: %s", e)
        ui_error(f"Sync failed: {e}")
        return 2

    finally:
        if wifi_connected:
            ui_info("Disconnecting from hotspot ...")
            WifiManager.disconnect()
            ui_ok("Disconnected")


if __name__ == "__main__":
    raise SystemExit(main())
