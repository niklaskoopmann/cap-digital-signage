"""Unit tests for scripts/xibo_sync/config.py."""

import pytest

from xibo_sync.config import getenv_bool, getenv_int, load_config

REQUIRED_ENV = {
    "CMS_BASE_URL": "http://192.168.1.1",
    "AUTH_MODE": "none",
    "LOCAL_MEDIA_DIR": "../media",
}


def _clear_optional_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CMS_CLIENT_ID",
        "CMS_CLIENT_SECRET",
        "MEDIA_EXTENSIONS",
        "COMPARE_MODE",
        "CALENDAR_JSON_PATH",
        "CALENDAR_DATASET_NAME",
        "CALENDAR_DATASET_CODE",
        "CALENDAR_UPLOAD_CANCELLED_EVENTS",
    ):
        monkeypatch.delenv(key, raising=False)


def _set_required_env(monkeypatch: pytest.MonkeyPatch, **overrides: str) -> None:
    _clear_optional_env(monkeypatch)
    values = {**REQUIRED_ENV, **overrides}
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_load_config_requires_cms_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CMS_BASE_URL", raising=False)
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("LOCAL_MEDIA_DIR", "../media")

    with pytest.raises(ValueError, match="CMS_BASE_URL"):
        load_config()


def test_load_config_requires_local_media_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMS_BASE_URL", "http://192.168.1.1")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.delenv("LOCAL_MEDIA_DIR", raising=False)

    with pytest.raises(ValueError, match="LOCAL_MEDIA_DIR"):
        load_config()


def test_load_config_rejects_invalid_auth_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, AUTH_MODE="basic")

    with pytest.raises(ValueError, match="AUTH_MODE"):
        load_config()


def test_load_config_oauth_requires_client_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, AUTH_MODE="oauth")

    with pytest.raises(ValueError, match="CMS_CLIENT_ID"):
        load_config()


def test_load_config_rejects_invalid_compare_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, COMPARE_MODE="fuzzy")

    with pytest.raises(ValueError, match="COMPARE_MODE"):
        load_config()


def test_load_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch)

    cfg = load_config()

    assert cfg.cms_base_url == "http://192.168.1.1"
    assert cfg.compare_mode == "filename"
    assert cfg.media_extensions == (".jpg", ".jpeg", ".png", ".gif", ".mp4")
    assert cfg.calendar_dataset_name == "office_calendar_events"
    assert cfg.calendar_upload_cancelled_events is False
    assert cfg.dry_run is False


def test_load_config_strips_trailing_slash_from_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_required_env(monkeypatch, CMS_BASE_URL="http://192.168.1.1/")

    cfg = load_config()

    assert cfg.cms_base_url == "http://192.168.1.1"


@pytest.mark.parametrize("raw", ["1", "true", "True", "yes", "y", "on"])
def test_getenv_bool_truthy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("FLAG", raw)
    assert getenv_bool("FLAG", False) is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", ""])
def test_getenv_bool_falsy_values(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    monkeypatch.setenv("FLAG", raw)
    assert getenv_bool("FLAG", True) is False


def test_getenv_bool_uses_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FLAG", raising=False)
    assert getenv_bool("FLAG", True) is True
    assert getenv_bool("FLAG", False) is False


def test_getenv_int_parses_and_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TIMEOUT", "45")
    assert getenv_int("TIMEOUT", 30) == 45

    monkeypatch.delenv("TIMEOUT", raising=False)
    assert getenv_int("TIMEOUT", 30) == 30
