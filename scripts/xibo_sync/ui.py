"""Rich terminal UI helpers used by the interactive sync workflow."""

from __future__ import annotations

import getpass
from typing import Dict, Optional

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

console = Console()


def ui_header(title: str, subtitle: Optional[str] = None) -> None:
    """Render a boxed header panel.

    Args:
        title: Primary heading text.
        subtitle: Optional secondary line shown beneath the heading.
    """
    txt = Text()
    txt.append(title, style="bold white")
    if subtitle:
        txt.append("\n")
        txt.append(subtitle, style="dim")
    console.print(Panel(txt, box=box.ROUNDED, border_style="cyan"))


def ui_error(msg: str) -> None:
    """Render an error message in a red panel.

    Args:
        msg: Error text to display.
    """
    console.print(Panel(Text(msg, style="bold red"), box=box.ROUNDED, border_style="red"))


def ui_info(msg: str) -> None:
    """Render a compact informational message.

    Args:
        msg: Informational text to display.
    """
    console.print(f"[cyan]ℹ[/cyan] {msg}")


def ui_ok(msg: str) -> None:
    """Render a success message.

    Args:
        msg: Success text to display.
    """
    console.print(f"[green]✅[/green] {msg}")


def ui_warn(msg: str) -> None:
    """Render a warning message.

    Args:
        msg: Warning text to display.
    """
    console.print(f"[yellow]⚠[/yellow] {msg}")


def ui_settings_table(env_data: Dict[str, str]) -> None:
    """Show a summary table of relevant settings.

    Args:
        env_data: Parsed ``.env`` key/value pairs to display.
    """
    keys = [
        "CMS_BASE_URL", "CMS_VERIFY_TLS", "CMS_TIMEOUT_SECONDS",
        "AUTH_MODE", "CMS_CLIENT_ID", "CMS_CLIENT_SECRET",
        "LOCAL_MEDIA_DIR", "CALENDAR_JSON_PATH", "CALENDAR_DATASET_NAME",
        "CALENDAR_DATASET_CODE", "CALENDAR_UPLOAD_CANCELLED_EVENTS",
        "MEDIA_EXTENSIONS", "COMPARE_MODE",
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
        if k == "CMS_CLIENT_SECRET":
            v = "******" if v else ""
        table.add_row(k, v)

    console.print(table)


def prompt_edit(key: str, current: str, help_text: str = "", validator=None) -> str:
    """Prompt for a configuration value, repeating until validation passes.

    Args:
        key: Environment variable name being edited.
        current: Current value used as the default.
        help_text: Optional helper text shown before the prompt.
        validator: Optional callable that raises when the input is invalid.

    Returns:
        The validated value entered by the operator.
    """
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
    """Prompt for a secret value without echoing input.

    Args:
        key: Secret name being edited.
        current: Current secret value used only to decide whether to keep it.

    Returns:
        The existing secret if the operator presses Enter, otherwise the new value.
    """
    shown = "******" if current else ""
    console.print(f"[dim]{key} (press ENTER to keep current: {shown})[/dim]")
    val = getpass.getpass(f"{key}: ").strip()
    return current if val == "" else val
