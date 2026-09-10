"""Generic Jinja rendering and browser image rendering helpers."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader, TemplateNotFound


class HtmlImageRenderer(Protocol):
    """Callable boundary for converting rendered HTML into a PNG."""

    def __call__(self, html_content: str, output_path: Path, width: int, height: int) -> None:
        ...


class PlaywrightRenderer:
    """Render HTML with headless Chromium at the requested viewport."""

    def __call__(self, html_content: str, output_path: Path, width: int, height: int) -> None:
        try:
            from playwright.sync_api import Error as PlaywrightError
            from playwright.sync_api import sync_playwright
        except ModuleNotFoundError as exc:
            raise RuntimeError(
                "Calendar PNG rendering requires Playwright. Install scripts/requirements.txt "
                "and run 'playwright install chromium'."
            ) from exc

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError(
                f"Calendar PNG rendering failed while preparing output path {output_path}. "
                "Check that the output directory is writable and retry."
            ) from exc
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={"width": width, "height": height})
                    page.set_content(html_content, wait_until="load")
                    try:
                        page.screenshot(path=str(output_path), type="png", full_page=False)
                    except (OSError, PlaywrightError) as exc:
                        raise RuntimeError(
                            f"calendar PNG rendering failed while writing screenshot to {output_path}. "
                            "Check that the output directory is writable and retry."
                        ) from exc
                finally:
                    browser.close()
        except PlaywrightError as exc:
            raise RuntimeError(
                "Playwright could not launch Chromium for calendar PNG rendering. "
                "Run 'playwright install chromium' in scripts/.venv and retry."
            ) from exc


def render_template(
    template_dir: Path,
    template_file: str,
    context: dict[str, Any],
) -> str:
    """Render a Jinja2 template with the supplied context.
    
    Args:
        template_dir: Directory containing templates (loader root).
        template_file: Filename within template_dir (e.g. 'template.html').
        context: Template context variables.
    
    Returns:
        Rendered template string.
        
    Raises:
        TemplateNotFound: If template_file does not exist in template_dir.
        jinja2.TemplateSyntaxError: If template has syntax errors.
    """
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=True,  # HTML-escape untrusted values
    )
    template = env.get_template(template_file)
    return template.render(context)


def load_view_config(
    views_dir: Path,
    view_type: str,
) -> dict[str, Any]:
    """Load and parse a view configuration JSON file.
    
    Args:
        views_dir: Directory containing view config files (e.g. 'scripts/templates/calendar/views/').
        view_type: View name, converted to '<view_type>.json' (e.g. 'today' -> 'today.json').
    
    Returns:
        Parsed JSON dict.
        
    Raises:
        FileNotFoundError: If <view_type>.json does not exist.
        json.JSONDecodeError: If file contains invalid JSON.
        ValueError: If JSON is valid but structure is invalid.
    """
    config_path = views_dir / f"{view_type}.json"
    
    if not config_path.exists():
        raise FileNotFoundError(f"View config not found: {config_path}")
    
    try:
        with open(config_path) as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in {config_path}: {exc}") from exc
    
    if not isinstance(data, dict):
        raise ValueError(f"Expected JSON dict in {config_path}, got {type(data).__name__}")
    
    return data


def package_html_to_zip(
    html_content: str,
    output_path: Path,
    package_name: str,
) -> Path:
    """Write an HTML document to a ZIP-based HTZ package.
    
    Args:
        html_content: HTML document content.
        output_path: Directory in which to write the package.
        package_name: Package filename (with or without .htz extension).
    
    Returns:
        Path to the created .htz file.
    """
    output_path.mkdir(parents=True, exist_ok=True)
    file_name = package_name if package_name.lower().endswith(".htz") else f"{package_name}.htz"
    package_path = output_path / file_name

    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("index.html", html_content)

    return package_path
