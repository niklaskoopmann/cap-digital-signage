"""Unit tests for scripts/xibo_sync/client.py."""

from __future__ import annotations

from typing import Any

import pytest

from xibo_sync.client import (
    XiboClient,
    _layout_id_from_payload,
    _upload_media_id,
    media_layout_ownership_tag,
)


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
    monkeypatch.setattr(client, "get_layout_by_name", lambda *args: {"layoutId": "113"})
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


def test_list_layouts_by_ownership_tag_uses_exact_tag_and_normalizes_wrapper(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)

    def request(method: str, url: str, *, params=None, **kwargs):
        assert (method, url) == ("GET", "http://cms/api/layout")
        assert params == {"tags": "xibo-sync-media:62", "embed": "tags", "length": 1000}
        return _Response({"data": {"layouts": [
            {"layoutId": "owned", "tags": [{"tag": "xibo-sync-media:62"}]},
            {"layoutId": "similar", "tags": ["xibo-sync-media:620"]},
        ]}})

    monkeypatch.setattr(client, "_request", request)

    assert client.list_layouts_by_ownership_tag("xibo-sync-media:62") == [
        {"layoutId": "owned", "tags": [{"tag": "xibo-sync-media:62"}]}
    ]


def test_list_layouts_by_ownership_tag_finds_exact_match_on_later_page(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    page = [{"layoutId": f"layout-{index}", "tags": ["xibo-sync-media:62"]} for index in range(1000)]
    calls = []

    def request(method: str, url: str, *, params=None, **kwargs):
        calls.append(params)
        if params.get("start") == 1000:
            return _Response({"data": [{"layoutId": "later", "tags": ["xibo-sync-media:62"]}]})
        return _Response({"data": page})

    monkeypatch.setattr(client, "_request", request)

    layouts = client.list_layouts_by_ownership_tag("xibo-sync-media:62")

    assert layouts[-1]["layoutId"] == "later"
    assert calls == [
        {"tags": "xibo-sync-media:62", "embed": "tags", "length": 1000},
        {"tags": "xibo-sync-media:62", "embed": "tags", "length": 1000, "start": 1000},
    ]


def test_cleanup_layout_filters_schedule_events_and_unassigns_known_groups_in_order(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[tuple[Any, ...]] = []

    monkeypatch.setattr(client, "_resolve_layout_for_safe_deletion", lambda layout, ownership_tag: "layout-1")
    monkeypatch.setattr(client, "list_schedule_events_for_layout", lambda layout_id, campaign_id: [
        {"id": "event-1", "layoutId": layout_id},
        {"id": "event-2", "campaign": {"layoutId": layout_id}},
    ])
    monkeypatch.setattr(client, "delete_schedule_event", lambda event_id, dry_run=False: calls.append(("event", event_id)))
    monkeypatch.setattr(
        client,
        "unassign_layout_from_known_displaygroups",
        lambda display_group_ids, layout_id, dry_run=False: calls.append(("groups", tuple(display_group_ids), layout_id)),
    )
    monkeypatch.setattr(client, "delete_layout", lambda layout_id, dry_run=False: calls.append(("layout", layout_id)))

    client.cleanup_layout_for_media(
        {"layoutId": "layout-1", "campaignId": "campaign-1", "tags": "xibo-sync,xibo-sync-media:62"},
        media_id="62", ownership_tag=media_layout_ownership_tag("62"),
        display_group_ids=["group-1", "group-2"],
    )

    assert calls == [
        ("event", "event-1"), ("event", "event-2"),
        ("groups", ("group-1", "group-2"), "layout-1"),
        ("layout", "layout-1"),
    ]


def test_cleanup_blocks_externally_opened_draft_with_exact_ownership_tag(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)

    monkeypatch.setattr(client, "_list_drafts_for_layout", lambda layout_id: [
        {"layoutId": "draft-2", "publishedStatusId": 2, "tags": ["xibo-sync-media:62"]}
    ])
    monkeypatch.setattr(client, "discard_layout_draft", lambda *args: pytest.fail("must not discard draft"))
    monkeypatch.setattr(client, "list_schedule_events_for_layout", lambda *args: pytest.fail("must not clean dependencies"))
    monkeypatch.setattr(client, "delete_layout", lambda *args: pytest.fail("must not delete layout"))

    with pytest.raises(RuntimeError, match="layout-1.*draft-2.*checkout owner.*Retain media"):
        client.cleanup_layout_for_media(
            {"layoutId": "layout-1", "campaignId": "campaign-1", "tags": ["xibo-sync-media:62"]},
            media_id="62",
            ownership_tag="xibo-sync-media:62",
        )


def test_schedule_lookup_filters_campaign_id_events_and_malformed_entries(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)

    def request(method: str, url: str, *, params=None, **kwargs):
        assert params == {"campaignId": "campaign-1", "eventTypeId": 1, "length": 1000}
        return _Response({"data": {"events": [
            {"id": "matching", "campaignId": "campaign-1", "layoutId": "layout-1"},
            {
                "id": "nested-matching",
                "campaign": {"campaignId": "campaign-1"},
                "layout": {"layoutId": "layout-1"},
            },
            {"id": "other", "campaignId": "campaign-2"},
            {"id": "unknown", "name": "not safe to delete"},
        ]}})

    monkeypatch.setattr(client, "_request", request)

    assert [event["id"] for event in client.list_schedule_events_for_layout("layout-1", "campaign-1")] == [
        "matching", "nested-matching"
    ]


def test_schedule_lookup_filters_by_campaign_id(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)

    def request(method: str, url: str, *, params=None, **kwargs):
        assert params == {"campaignId": "campaign-1", "eventTypeId": 1, "length": 1000}
        return _Response({"data": [
            {"id": "matching", "campaignId": "campaign-1", "layoutId": "layout-1"},
            {"id": "other", "campaignId": "campaign-2"},
            {"id": "layout-match", "layoutId": "layout-1"},
        ]})

    monkeypatch.setattr(client, "_request", request)

    assert [event["id"] for event in client.list_schedule_events_for_layout("layout-1", "campaign-1")] == [
        "matching"
    ]


def test_schedule_lookup_filters_events_with_both_campaign_and_layout_ids(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)

    def request(method: str, url: str, *, params=None, **kwargs):
        assert params == {"campaignId": "campaign-1", "eventTypeId": 1, "length": 1000}
        return _Response({"data": [
            {"id": "matching", "campaignId": "campaign-1", "layoutId": "layout-1"},
            {"id": "other-layout", "campaignId": "campaign-1", "layoutId": "layout-2"},
        ]})

    monkeypatch.setattr(client, "_request", request)

    assert [event["id"] for event in client.list_schedule_events_for_layout("layout-1", "campaign-1")] == [
        "matching"
    ]


def test_get_layout_by_name_rejects_ambiguous_exact_matches(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    monkeypatch.setattr(
        client,
        "_request",
        lambda *args, **kwargs: _Response({"data": [
            {"layoutId": "layout-1", "name": "Calendar Today"},
            {"layoutId": "layout-2", "name": "Calendar Today"},
        ]}),
    )

    with pytest.raises(RuntimeError, match="Calendar Today.*ambiguous"):
        client.get_layout_by_name("Calendar Today")


def test_cleanup_refuses_layout_without_exact_ownership_tag(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    monkeypatch.setattr(client, "list_schedule_events_for_layout", lambda layout_id: pytest.fail("must not mutate unowned layout"))

    with pytest.raises(RuntimeError, match="layoutId=layout-1"):
        client.cleanup_layout_for_media(
            {"layoutId": "layout-1", "tags": ["xibo-sync"]},
            media_id="62", ownership_tag="xibo-sync-media:62",
        )


def test_unassign_layout_uses_repeated_layout_id_form_field(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls = []

    def request(method: str, url: str, *, data=None, **kwargs):
        calls.append((method, url, data))
        return _Response({})

    monkeypatch.setattr(client, "_request", request)

    client.unassign_layout_from_displaygroup("group-1", "layout-1")

    assert calls == [
        ("POST", "http://cms/api/displaygroup/group-1/layout/unassign", [("layoutId[]", "layout-1")])
    ]


def test_unassign_layout_from_known_displaygroups_calls_each_known_group(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        client, "unassign_layout_from_displaygroup",
        lambda group_id, layout_id, dry_run=False: calls.append((group_id, layout_id)),
    )

    client.unassign_layout_from_known_displaygroups(["group-1", "group-2"], "layout-1")

    assert calls == [("group-1", "layout-1"), ("group-2", "layout-1")]


def test_unassign_layout_from_known_displaygroups_skips_falsy_ids(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[str] = []
    monkeypatch.setattr(
        client, "unassign_layout_from_displaygroup",
        lambda group_id, layout_id, dry_run=False: calls.append(group_id),
    )

    client.unassign_layout_from_known_displaygroups(["", None, "group-1"], "layout-1")

    assert calls == ["group-1"]


def test_unassign_layout_from_known_displaygroups_honors_dry_run(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        client, "unassign_layout_from_displaygroup",
        lambda group_id, layout_id, dry_run=False: calls.append((group_id, dry_run)),
    )

    client.unassign_layout_from_known_displaygroups(["group-1"], "layout-1", dry_run=True)

    assert calls == [("group-1", True)]


def test_unassign_layout_treats_404_as_success_noop(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    response = _Response({})
    response.ok = False
    response.status_code = 404
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    client.unassign_layout_from_displaygroup("group-1", "layout-1")


def test_unassign_layout_raises_on_non_404_error(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    response = _Response({})
    response.ok = False
    response.status_code = 500
    response.text = "CMS exploded"
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    with pytest.raises(RuntimeError, match="layoutId=layout-1.*displayGroupId=group-1.*CMS exploded"):
        client.unassign_layout_from_displaygroup("group-1", "layout-1")


def test_get_layout_by_id_returns_first_match(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls = []

    def request(method: str, url: str, *, params=None, **kwargs):
        calls.append((method, url, params))
        return _Response({"data": [{"layoutId": "112", "publishedStatusId": "1"}]})

    monkeypatch.setattr(client, "_request", request)

    assert client.get_layout_by_id("112") == {"layoutId": "112", "publishedStatusId": "1"}
    assert calls[0][2] == {"layoutId": "112", "showDrafts": 1, "embed": "tags", "length": 1}


def test_get_layout_by_id_returns_none_when_missing(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: _Response({"data": []}))

    assert client.get_layout_by_id("112") is None


def test_get_layout_by_id_returns_none_on_error_response(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    response = _Response({})
    response.ok = False
    response.status_code = 500
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    assert client.get_layout_by_id("112") is None


def test_cleanup_stops_before_groups_and_layout_when_schedule_delete_fails(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    monkeypatch.setattr(client, "_resolve_layout_for_safe_deletion", lambda layout, ownership_tag: "layout-1")
    monkeypatch.setattr(client, "list_schedule_events_for_layout", lambda layout_id, campaign_id: [{"id": "event-1", "campaignId": campaign_id}])
    monkeypatch.setattr(client, "delete_schedule_event", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("event-1 failed")))
    monkeypatch.setattr(client, "unassign_layout_from_known_displaygroups", lambda *args, **kwargs: pytest.fail("groups must not be unassigned after failure"))
    monkeypatch.setattr(client, "delete_layout", lambda *args, **kwargs: pytest.fail("layout must not be deleted after failure"))

    with pytest.raises(RuntimeError, match="event-1 failed"):
        client.cleanup_layout_for_media(
            {"layoutId": "layout-1", "campaignId": "campaign-1", "tags": ["xibo-sync-media:62"]},
            media_id="62", ownership_tag="xibo-sync-media:62",
        )


@pytest.mark.parametrize("method", ["delete_schedule_event", "delete_layout", "unassign_layout_from_displaygroup"])
def test_delete_mutators_support_dry_run_without_request(monkeypatch, method) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: pytest.fail("dry-run must not request"))

    if method == "delete_schedule_event":
        client.delete_schedule_event("event-1", dry_run=True)
    elif method == "delete_layout":
        client.delete_layout("layout-1", dry_run=True)
    else:
        client.unassign_layout_from_displaygroup("group-1", "layout-1", dry_run=True)


def test_cleanup_dry_run_does_not_call_mutators(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls = []
    monkeypatch.setattr(client, "_resolve_layout_for_safe_deletion", lambda layout, ownership_tag: "layout-1")
    monkeypatch.setattr(client, "list_schedule_events_for_layout", lambda *args: [{"id": "event-1"}])
    monkeypatch.setattr(client, "delete_schedule_event", lambda event_id, dry_run=False: calls.append(("event", event_id, dry_run)))
    monkeypatch.setattr(
        client,
        "unassign_layout_from_known_displaygroups",
        lambda display_group_ids, layout_id, dry_run=False: calls.append(("groups", tuple(display_group_ids), dry_run)),
    )
    monkeypatch.setattr(client, "delete_layout", lambda layout_id, dry_run=False: calls.append(("layout", layout_id, dry_run)))

    client.cleanup_layout_for_media(
        {"layoutId": "layout-1", "campaignId": "campaign-1", "tags": ["xibo-sync-media:62"]},
        media_id="62", ownership_tag="xibo-sync-media:62", dry_run=True,
        display_group_ids=["group-1"],
    )

    assert calls == [("event", "event-1", True), ("groups", ("group-1",), True), ("layout", "layout-1", True)]


def test_layout_mutator_propagates_cms_error_with_resource_id(monkeypatch) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    response = _Response({})
    response.ok = False
    response.status_code = 500
    response.text = "CMS exploded"
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: response)

    with pytest.raises(RuntimeError, match="layoutId=layout-1.*CMS exploded"):
        client.delete_layout("layout-1")


def test_calendar_deployment_publish_failure_propagates_and_stops_followup(monkeypatch, tmp_path) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[str] = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: _Response({"data": {}}))
    monkeypatch.setattr(client, "upload_html_package", lambda *args: {"mediaId": "media-1"})
    monkeypatch.setattr(client, "get_or_create_calendar_layout", lambda *args: {"layoutId": "layout-1"})
    monkeypatch.setattr(client, "assign_html_package_to_layout", lambda *args, **kwargs: calls.append("assign"))

    def publish(*args, **kwargs):
        calls.append("publish")
        raise RuntimeError("Publish layoutId=layout-1 failed (500): locked")

    monkeypatch.setattr(client, "publish_layout", publish)
    monkeypatch.setattr(client, "tag_layout", lambda *args, **kwargs: calls.append("tag"))

    with pytest.raises(RuntimeError, match="Publish layoutId=layout-1"):
        client.deploy_calendar_package_to_layout(
            "Calendar Today", tmp_path / "calendar.htz", ["calendar-html"],
            publish=True, assign_to_display_group_id="group-1", immediate_show=True, dry_run=False,
        )

    assert calls == ["assign", "publish"]


def test_calendar_deployment_requires_authoritative_post_publish_resolution(monkeypatch, tmp_path) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[str] = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: _Response({"data": {}}))
    monkeypatch.setattr(client, "upload_html_package", lambda *args: {"mediaId": "media-1"})
    monkeypatch.setattr(client, "get_or_create_calendar_layout", lambda *args: {"layoutId": "layout-1"})
    monkeypatch.setattr(client, "assign_html_package_to_layout", lambda *args, **kwargs: calls.append("assign"))
    monkeypatch.setattr(client, "publish_layout", lambda *args, **kwargs: calls.append("publish"))
    monkeypatch.setattr(client, "get_layout_by_name", lambda *args, **kwargs: None)
    monkeypatch.setattr(client, "tag_layout", lambda *args, **kwargs: calls.append("tag"))

    with pytest.raises(RuntimeError, match="Published calendar layout.*Calendar Today"):
        client.deploy_calendar_package_to_layout(
            "Calendar Today", tmp_path / "calendar.htz", ["calendar-html"],
            publish=True, assign_to_display_group_id="group-1", immediate_show=True, dry_run=False,
        )

    assert calls == ["assign", "publish"]


def test_calendar_deployment_propagates_layout_tagging_failure(monkeypatch, tmp_path) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[str] = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: _Response({"data": {}}))
    monkeypatch.setattr(client, "upload_html_package", lambda *args: {"mediaId": "media-1"})
    monkeypatch.setattr(client, "get_or_create_calendar_layout", lambda *args: {"layoutId": "layout-1"})
    monkeypatch.setattr(client, "assign_html_package_to_layout", lambda *args, **kwargs: calls.append("assign"))
    monkeypatch.setattr(client, "publish_layout", lambda *args, **kwargs: calls.append("publish"))
    monkeypatch.setattr(client, "get_layout_by_name", lambda *args, **kwargs: {"layoutId": "layout-1"})

    def fail_tag(*args, **kwargs):
        calls.append("tag")
        raise RuntimeError("tag layoutId=layout-1 failed")

    monkeypatch.setattr(client, "tag_layout", fail_tag)
    monkeypatch.setattr(client, "assign_layouts_to_displaygroup", lambda *args, **kwargs: calls.append("group"))

    with pytest.raises(RuntimeError, match="tag layoutId=layout-1 failed"):
        client.deploy_calendar_package_to_layout(
            "Calendar Today", tmp_path / "calendar.htz", ["calendar-html"],
            publish=True, assign_to_display_group_id="group-1", immediate_show=True, dry_run=False,
        )

    assert calls == ["assign", "publish", "tag"]


def test_calendar_deployment_blocks_display_assignment_when_final_publish_fails(monkeypatch, tmp_path) -> None:
    client = XiboClient("http://cms", verify_tls=False, timeout=10)
    calls: list[str] = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: _Response({"data": {}}))
    monkeypatch.setattr(client, "upload_html_package", lambda *args: {"mediaId": "media-1"})
    monkeypatch.setattr(client, "get_or_create_calendar_layout", lambda *args: {"layoutId": "layout-1"})
    monkeypatch.setattr(client, "assign_html_package_to_layout", lambda *args, **kwargs: calls.append("assign-package"))

    def publish(*args, **kwargs):
        calls.append("publish")
        if calls.count("publish") == 2:
            raise RuntimeError("Publish layoutId=layout-1 failed (500): locked")

    monkeypatch.setattr(client, "publish_layout", publish)
    monkeypatch.setattr(client, "get_layout_by_name", lambda *args, **kwargs: {"layoutId": "layout-1"})
    monkeypatch.setattr(client, "tag_layout", lambda *args, **kwargs: calls.append("tag"))
    monkeypatch.setattr(client, "assign_layouts_to_displaygroup", lambda *args, **kwargs: calls.append("group"))

    with pytest.raises(RuntimeError, match="Publish layoutId=layout-1"):
        client.deploy_calendar_package_to_layout(
            "Calendar Today", tmp_path / "calendar.htz", ["calendar-html"],
            publish=True, assign_to_display_group_id="group-1", immediate_show=True, dry_run=False,
        )

    assert calls == ["assign-package", "publish", "tag", "publish"]