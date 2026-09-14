import pytest

from calendar_render_service.config import load_config


def test_service_config_requires_dedicated_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMS_BASE_URL", "http://cms-web")
    monkeypatch.delenv("CMS_CLIENT_ID", raising=False)
    monkeypatch.delenv("CMS_CLIENT_SECRET", raising=False)

    with pytest.raises(ValueError, match="CMS_CLIENT_ID"):
        load_config()


def test_service_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMS_BASE_URL", "http://cms-web")
    monkeypatch.setenv("CMS_CLIENT_ID", "service-id")
    monkeypatch.setenv("CMS_CLIENT_SECRET", "service-secret")
    monkeypatch.delenv("CALENDAR_EVENT_RETENTION_DAYS", raising=False)
    monkeypatch.delenv("CALENDAR_RENDER_SCHEDULE_SECONDS", raising=False)
    monkeypatch.delenv("CALENDAR_CLEANUP_OLD_VIEW_UPLOADS", raising=False)

    cfg = load_config()

    assert cfg.calendar_event_retention_days == 30
    assert cfg.calendar_html_views == ("today", "this_week", "next_2_weeks")
    assert cfg.schedule_seconds == 86400
    assert cfg.cleanup_old_view_uploads is True
    assert cfg.log_level == "INFO"


def test_service_config_override_cleanup_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CMS_BASE_URL", "http://cms-web")
    monkeypatch.setenv("CMS_CLIENT_ID", "service-id")
    monkeypatch.setenv("CMS_CLIENT_SECRET", "service-secret")
    monkeypatch.setenv("CALENDAR_CLEANUP_OLD_VIEW_UPLOADS", "false")

    cfg = load_config()

    assert cfg.cleanup_old_view_uploads is False