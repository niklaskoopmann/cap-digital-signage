"""Unit tests for scripts/xibo_sync/client.py."""

from __future__ import annotations

from typing import Any

from xibo_sync.client import XiboClient, _layout_id_from_payload, _upload_media_id


class _Response:
    ok = True
    status_code = 200
    text = "ok"

    def __init__(self, payload: Any) -> None:
        self.payload = payload

    def json(self) -> Any:
        return self.payload


def test_assign_html_package_uses_layout_search_endpoint(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[tuple[str, str, dict[str, str] | None, Any]] = []

    def request(method: str, url: str, *, params=None, data=None, **kwargs):
        calls.append((method, url, params, data))
        if url.endswith("/layout"):
            return _Response({"data": [{"regions": [{"regionPlaylist": {"playlistId": "playlist-7"}}]}]})
        return _Response({})

    monkeypatch.setattr(client, "_request", request)

    client.assign_html_package_to_layout("layout-7", "media-8", dry_run=False)

    assert calls[0][:3] == (
        "GET",
        "http://cms/api/layout",
        {"layoutId": "layout-7", "embed": "regions,playlists", "length": 1},
    )
    assert calls[1][0:2] == ("POST", "http://cms/api/playlist/library/assign/playlist-7")


def test_assign_html_package_creates_region_when_layout_is_empty(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    layout_responses = iter([
        {"data": [{"regions": []}]},
        {"data": [{"regions": [{"regionPlaylist": {"playlistId": "playlist-9"}}]}]},
    ])
    calls: list[tuple[str, str, Any, Any]] = []

    def request(method: str, url: str, *, params=None, data=None, **kwargs):
        calls.append((method, url, params, data))
        if method == "GET":
            return _Response(next(layout_responses))
        return _Response({})

    monkeypatch.setattr(client, "_request", request)

    client.assign_html_package_to_layout("layout-9", "media-8", dry_run=False)

    assert calls[1] == (
        "POST",
        "http://cms/api/region/layout-9",
        None,
        {"type": "frame", "width": 1920, "height": 1080, "top": 0, "left": 0},
    )
    assert calls[2][0:2] == ("GET", "http://cms/api/layout")
    assert calls[3][0:2] == ("POST", "http://cms/api/playlist/library/assign/playlist-9")


def test_upload_media_id_prefers_top_level_id() -> None:
    assert _upload_media_id({"mediaId": 62, "files": [{"name": "package.htz"}]}) == "62"


def test_upload_media_id_reads_nested_file_id() -> None:
    assert _upload_media_id({"files": [{"mediaId": 62}]}) == "62"


def test_upload_media_id_returns_empty_for_missing_id() -> None:
    assert _upload_media_id({"files": [{"name": "package.htz"}]}) == ""


def test_layout_id_from_payload_reads_nested_checkout_response() -> None:
    assert _layout_id_from_payload({"data": [{"layout": {"layoutId": 113}}]}) == "113"


def test_get_draft_layout_id_filters_for_drafts(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls = []

    def request(method: str, url: str, *, params=None, **kwargs):
        calls.append((method, url, params))
        return _Response({"data": [{"layoutId": 113}]})

    monkeypatch.setattr(client, "_request", request)

    assert client.get_draft_layout_id("112") == "113"
    assert calls[0][2] == {"layoutId": "112", "showDrafts": 1, "publishedStatusId": 2, "length": 1}


def test_deploy_discards_stale_checkout_before_assigning(monkeypatch, tmp_path) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[tuple[str, str]] = []
    stale_checkout = _Response({"message": "already checked out"})
    stale_checkout.ok = False
    stale_checkout.status_code = 422
    checkout_responses = iter([stale_checkout, _Response({"data": {"layoutId": 113}})])

    def request(method: str, url: str, **kwargs):
        calls.append((method, url))
        if url.endswith("/layout/checkout/112"):
            response = next(checkout_responses)
            return response
        return _Response({})

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(client, "upload_html_package", lambda *args: {"mediaId": "62"})
    monkeypatch.setattr(client, "get_or_create_calendar_layout", lambda *args: {"layoutId": "112"})
    assigned: list[str] = []
    monkeypatch.setattr(client, "assign_html_package_to_layout", lambda layout_id, media_id, dry_run: assigned.append(layout_id))
    monkeypatch.setattr(client, "tag_layout", lambda *args, **kwargs: None)

    client.deploy_calendar_package_to_layout(
        "Calendar - Today", tmp_path / "calendar.htz", ["calendar-html"],
        publish=False, assign_to_display_group_id=None, immediate_show=False, dry_run=False,
    )

    assert assigned == ["113"]
    assert calls[:3] == [
        ("PUT", "http://cms/api/layout/checkout/112"),
        ("PUT", "http://cms/api/layout/discard/112"),
        ("PUT", "http://cms/api/layout/checkout/112"),
    ]


def test_find_library_item_by_name_uses_exact_match(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)

    class _NameResponse(_Response):
        pass

    def request(method: str, url: str, *, params=None, **kwargs):
        assert method == "GET"
        assert url == "http://cms/api/library"
        assert params == {
            "media": "package.htz",
            "length": 10,
            "sortBy": "modifiedDt",
            "sortDir": "desc",
        }
        return _NameResponse({"data": [{"name": "other.htz"}, {"name": "package.htz", "mediaId": 62}]})

    monkeypatch.setattr(client, "_request", request)

    assert client.find_library_item_by_name("package.htz") == {"name": "package.htz", "mediaId": 62}