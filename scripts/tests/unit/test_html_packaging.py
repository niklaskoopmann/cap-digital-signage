"""Unit tests for scripts/xibo_sync/html_packaging.py."""

from __future__ import annotations

import json
import sys
import types
import zipfile
from pathlib import Path

import pytest

from xibo_sync import html_packaging


def _fake_playwright_module(error_type: type[Exception], launch_error: Exception | None = None, screenshot_error: Exception | None = None) -> types.ModuleType:
    class FakePage:
        def set_content(self, html_content: str, wait_until: str) -> None:
            pass

        def screenshot(self, **kwargs: object) -> None:
            if screenshot_error is not None:
                raise screenshot_error

    class FakeBrowser:
        def new_page(self, **kwargs: object) -> FakePage:
            return FakePage()

        def close(self) -> None:
            pass

    class FakeChromium:
        def launch(self, **kwargs: object) -> FakeBrowser:
            if launch_error is not None:
                raise launch_error
            return FakeBrowser()

    class FakePlaywright:
        chromium = FakeChromium()

    class PlaywrightContext:
        def __enter__(self) -> FakePlaywright:
            return FakePlaywright()

        def __exit__(self, *args: object) -> None:
            pass

    module = types.ModuleType("playwright.sync_api")
    module.Error = error_type
    module.sync_playwright = lambda: PlaywrightContext()
    return module


def test_playwright_renderer_reports_missing_dependency(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setitem(sys.modules, "playwright", None)

    with pytest.raises(RuntimeError, match="Install scripts/requirements.txt.*playwright install chromium"):
        html_packaging.PlaywrightRenderer()("<html></html>", tmp_path / "calendar.png", 1920, 1080)


def test_playwright_renderer_reports_missing_browser(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakePlaywrightError(Exception):
        pass

    sync_api = _fake_playwright_module(FakePlaywrightError, launch_error=FakePlaywrightError("browser missing"))
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    with pytest.raises(RuntimeError, match="could not launch Chromium.*playwright install chromium"):
        html_packaging.PlaywrightRenderer()("<html></html>", tmp_path / "calendar.png", 1920, 1080)


def test_playwright_renderer_reports_screenshot_write_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class FakePlaywrightError(Exception):
        pass

    sync_api = _fake_playwright_module(FakePlaywrightError, screenshot_error=OSError("disk full"))
    monkeypatch.setitem(sys.modules, "playwright", types.ModuleType("playwright"))
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)

    with pytest.raises(RuntimeError, match="calendar PNG rendering failed"):
        html_packaging.PlaywrightRenderer()("<html></html>", tmp_path / "calendar.png", 1920, 1080)


def test_render_template_substitutes_context() -> None:
    """render_template should substitute context variables in Jinja2 templates."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        template_dir = Path(tmp)
        (template_dir / "test.html").write_text("<h1>{{ title }}</h1>")

        result = html_packaging.render_template(
            template_dir,
            "test.html",
            {"title": "Hello World"},
        )

        assert "<h1>Hello World</h1>" in result


def test_render_template_html_escapes_untrusted_values() -> None:
    """render_template should HTML-escape untrusted values to prevent XSS."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        template_dir = Path(tmp)
        (template_dir / "test.html").write_text("<p>{{ subject }}</p>")

        result = html_packaging.render_template(
            template_dir,
            "test.html",
            {"subject": "<script>alert('xss')</script>"},
        )

        assert "&lt;script&gt;" in result
        assert "&lt;/script&gt;" in result
        assert "<script>" not in result


def test_render_template_raises_for_missing_template() -> None:
    """render_template should raise TemplateNotFound for missing template file."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        template_dir = Path(tmp)

        with pytest.raises(Exception) as exc_info:  # jinja2.TemplateNotFound
            html_packaging.render_template(template_dir, "nonexistent.html", {})

        assert "nonexistent.html" in str(exc_info.value).lower() or "does not exist" in str(exc_info.value).lower()


def test_load_view_config_returns_parsed_json() -> None:
    """load_view_config should parse and return a view config JSON file."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        views_dir = Path(tmp)
        (views_dir / "today.json").write_text('{"title": "Today", "window_days": 1, "layout_name": "Calendar Today"}')

        config = html_packaging.load_view_config(views_dir, "today")

        assert config["title"] == "Today"
        assert config["window_days"] == 1
        assert config["layout_name"] == "Calendar Today"


def test_load_view_config_raises_for_missing_file() -> None:
    """load_view_config should raise FileNotFoundError for missing config file."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        views_dir = Path(tmp)

        with pytest.raises(FileNotFoundError) as exc_info:
            html_packaging.load_view_config(views_dir, "nonexistent")

        assert "nonexistent.json" in str(exc_info.value)


def test_load_view_config_raises_for_invalid_json() -> None:
    """load_view_config should raise ValueError for invalid JSON."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        views_dir = Path(tmp)
        (views_dir / "bad.json").write_text("{invalid json}")

        with pytest.raises(ValueError) as exc_info:
            html_packaging.load_view_config(views_dir, "bad")

        assert "invalid json" in str(exc_info.value).lower()


def test_load_view_config_raises_for_non_dict_json() -> None:
    """load_view_config should raise ValueError when JSON is not a dict."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        views_dir = Path(tmp)
        (views_dir / "array.json").write_text('["item1", "item2"]')

        with pytest.raises(ValueError) as exc_info:
            html_packaging.load_view_config(views_dir, "array")

        assert "expected json dict" in str(exc_info.value).lower()


def test_package_html_to_zip_creates_htz_file(tmp_path: Path) -> None:
    """package_html_to_zip should create a .htz file with index.html inside."""
    html_content = "<html><body>Test</body></html>"

    package_path = html_packaging.package_html_to_zip(
        html_content,
        tmp_path,
        "test_package",
    )

    assert package_path == tmp_path / "test_package.htz"
    assert package_path.exists()

    with zipfile.ZipFile(package_path) as archive:
        assert archive.namelist() == ["index.html"]
        assert archive.read("index.html").decode("utf-8") == html_content


def test_package_html_to_zip_creates_output_directory_if_missing(tmp_path: Path) -> None:
    """package_html_to_zip should create the output directory if it doesn't exist."""
    output_dir = tmp_path / "nested" / "output"
    html_content = "<html>Test</html>"

    package_path = html_packaging.package_html_to_zip(
        html_content,
        output_dir,
        "test_package",
    )

    assert output_dir.exists()
    assert package_path.exists()


def test_package_html_to_zip_handles_htz_extension(tmp_path: Path) -> None:
    """package_html_to_zip should handle package names with or without .htz extension."""
    html_content = "<html>Test</html>"

    # With .htz extension
    package_path1 = html_packaging.package_html_to_zip(
        html_content,
        tmp_path,
        "package.htz",
    )
    assert package_path1 == tmp_path / "package.htz"

    # Without .htz extension
    package_path2 = html_packaging.package_html_to_zip(
        html_content,
        tmp_path,
        "another_package",
    )
    assert package_path2 == tmp_path / "another_package.htz"
