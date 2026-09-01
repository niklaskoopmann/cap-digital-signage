"""Offline integration tests for media layout publication and cleanup."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from xibo_sync import app
from xibo_sync.config import load_config


class FakeSyncClient:
    def __init__(self, base_url: str, verify_tls: bool, timeout: int) -> None:
        self.calls: list[tuple[str, Any]] = []

    def health_check(self) -> None:
        self.calls.append(("health_check", None))

    def list_library(self, managed_tag: str, folder_id: str | None) -> list[dict[str, str]]:
        self.calls.append(("list_library", (managed_tag, folder_id)))
        return [
            {"mediaId": "media-owned", "name": "owned.png", "tags": "xibo-sync"},
            {"mediaId": "media-unrelated", "name": "unrelated.png", "tags": "xibo-sync"},
        ]

    def upload_media(self, **kwargs: Any) -> dict[str, str]:
        self.calls.append(("upload_media", kwargs))
        return {"mediaId": "media-new", "name": "new.png"}

    def create_fullscreen_layout(self, media: dict[str, str], dry_run: bool) -> dict[str, str]:
        self.calls.append(("create_fullscreen_layout", (media, dry_run)))
        return {"layoutId": "draft-layout", "layout": "new.png fullscreen"}

    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))

    def get_layout_by_name(self, name: str) -> dict[str, str]:
        self.calls.append(("get_layout_by_name", name))
        return {"layoutId": "published-layout", "layout": name}

    def get_draft_layout_id(self, layout_id: str) -> str | None:
        self.calls.append(("get_draft_layout_id", layout_id))
        return None

    def get_layout_by_id(self, layout_id: str) -> dict[str, str] | None:
        self.calls.append(("get_layout_by_id", layout_id))
        return None

    def tag_layout(self, layout_id: str, tags: list[str], dry_run: bool = False) -> None:
        self.calls.append(("tag_layout", (layout_id, tags, dry_run)))

    def list_layouts_by_ownership_tag(self, ownership_tag: str) -> list[dict[str, Any]]:
        self.calls.append(("list_layouts_by_ownership_tag", ownership_tag))
        if ownership_tag == "xibo-sync-media:media-owned":
            return [{"layoutId": "owned-layout", "tags": [ownership_tag]}]
        return []

    def cleanup_layout_for_media(
        self,
        layout: dict[str, Any],
        media_id: str,
        ownership_tag: str,
        display_group_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.calls.append(
            ("cleanup_layout_for_media", (layout["layoutId"], media_id, ownership_tag, tuple(display_group_ids or []), dry_run))
        )

    def delete_media(self, media_id: str, dry_run: bool) -> None:
        self.calls.append(("delete_media", (media_id, dry_run)))


class DraftPublishRetryFakeSyncClient(FakeSyncClient):
    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))
        if layout_id == "draft-layout":
            raise RuntimeError("Publish layoutId=draft-layout failed (404): layout not found")


class Non404DraftPublishFailureFakeSyncClient(DraftPublishRetryFakeSyncClient):
    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))
        if layout_id == "draft-layout":
            raise RuntimeError("Publish layoutId=draft-layout failed (500): CMS unavailable")


class SameIdDraftPublishRetryFakeSyncClient(DraftPublishRetryFakeSyncClient):
    def get_layout_by_name(self, name: str) -> dict[str, str]:
        self.calls.append(("get_layout_by_name", name))
        return {"layoutId": "draft-layout", "layout": name}


class StatefulLayoutLifecycleFakeSyncClient(FakeSyncClient):
    """Model the Xibo 4.4 behavior where tagging a published layout opens a draft."""

    def __init__(self, base_url: str, verify_tls: bool, timeout: int) -> None:
        super().__init__(base_url, verify_tls, timeout)
        self.layout_state = "draft"

    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))
        if self.layout_state != "draft":
            raise RuntimeError("Xibo only publishes a draft")
        self.layout_state = "published"

    def tag_layout(self, layout_id: str, tags: list[str], dry_run: bool = False) -> None:
        self.calls.append(("tag_layout", (layout_id, tags, dry_run)))
        if self.layout_state != "published":
            raise RuntimeError("Xibo only tags a published layout in this model")
        self.layout_state = "draft"


class AmbiguousDraftPublishRetryFakeSyncClient(DraftPublishRetryFakeSyncClient):
    def get_layout_by_name(self, name: str) -> dict[str, str]:
        self.calls.append(("get_layout_by_name", name))
        raise RuntimeError(f"Layout name={name!r} is ambiguous")


class TagSpawnsNewDraftFakeSyncClient(FakeSyncClient):
    """Model Xibo 4.4 where tagging an already-published layout opens a new draft
    with a different layoutId, so the post-tag publish 404s on the stale id.
    """

    def __init__(self, base_url: str, verify_tls: bool, timeout: int) -> None:
        super().__init__(base_url, verify_tls, timeout)
        self.pending_draft_id: str | None = None

    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))
        if layout_id == "published-layout" and self.pending_draft_id:
            raise RuntimeError("Publish layoutId=published-layout failed (404): Layout not found")
        if layout_id == self.pending_draft_id:
            self.pending_draft_id = None

    def get_draft_layout_id(self, layout_id: str) -> str | None:
        self.calls.append(("get_draft_layout_id", layout_id))
        return self.pending_draft_id

    def tag_layout(self, layout_id: str, tags: list[str], dry_run: bool = False) -> None:
        self.calls.append(("tag_layout", (layout_id, tags, dry_run)))
        self.pending_draft_id = "tag-draft-layout"


class AlreadyPublishedNoDraftFakeSyncClient(FakeSyncClient):
    """Model Xibo 4.4 where publishing an already-published layout with no
    pending draft always 404s, because the draft row is consumed by the first
    successful publish and there is nothing left to publish on retry.
    """

    def __init__(self, base_url: str, verify_tls: bool, timeout: int) -> None:
        super().__init__(base_url, verify_tls, timeout)
        self.published_once = False

    def publish_layout(self, layout_id: str, dry_run: bool = False) -> None:
        self.calls.append(("publish_layout", (layout_id, dry_run)))
        if layout_id == "draft-layout" and not self.published_once:
            self.published_once = True
            return
        raise RuntimeError(f"Publish layoutId={layout_id} failed (404): Layout not found")

    def get_layout_by_id(self, layout_id: str) -> dict[str, str] | None:
        self.calls.append(("get_layout_by_id", layout_id))
        return {"layoutId": layout_id, "publishedStatusId": "1"}


class FailingCleanupFakeSyncClient(FakeSyncClient):
    def __init__(self, base_url: str, verify_tls: bool, timeout: int, failure: str) -> None:
        super().__init__(base_url, verify_tls, timeout)
        self.failure = failure

    def cleanup_layout_for_media(
        self,
        layout: dict[str, Any],
        media_id: str,
        ownership_tag: str,
        display_group_ids: list[str] | None = None,
        dry_run: bool = False,
    ) -> None:
        self.calls.append(
            ("cleanup_layout_for_media", (layout["layoutId"], media_id, ownership_tag, tuple(display_group_ids or []), dry_run))
        )
        raise RuntimeError(self.failure)


class ExternallyOpenedDraftFakeSyncClient(FailingCleanupFakeSyncClient):
    """Model an active human draft that retains the canonical ownership tag."""

    def __init__(self, base_url: str, verify_tls: bool, timeout: int) -> None:
        super().__init__(
            base_url,
            verify_tls,
            timeout,
            "Cannot safely clean canonical layoutId=owned-layout while active draft layoutId(s)=['external-draft'] "
            "exist: the Xibo API does not expose a reliable checkout owner. Retain media and resolve the draft in Xibo.",
        )


def test_stateful_xibo_model_keeps_tagged_layout_locked_until_final_publish() -> None:
    client = StatefulLayoutLifecycleFakeSyncClient("http://cms", False, 10)

    client.publish_layout("draft-layout")
    client.tag_layout("published-layout", ["xibo-sync-media:media-new"])
    assert client.layout_state == "draft"

    client.publish_layout("published-layout")
    assert client.layout_state == "published"


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    local_dir: Path,
    *,
    dry_run: bool = False,
    delete_layout_with_media: bool = True,
) -> None:
    monkeypatch.setenv("CMS_BASE_URL", "http://cms")
    monkeypatch.setenv("AUTH_MODE", "none")
    monkeypatch.setenv("LOCAL_MEDIA_DIR", str(local_dir))
    monkeypatch.setenv("COMPARE_MODE", "filename")
    monkeypatch.setenv("UPLOAD_NEW_LOCAL", "true")
    monkeypatch.setenv("DELETE_REMOTE_NOT_LOCAL", "true")
    monkeypatch.setenv("DELETE_LAYOUT_WITH_MEDIA", "true" if delete_layout_with_media else "false")
    monkeypatch.setenv("CREATE_LAYOUT_PER_UPLOAD", "true")
    monkeypatch.setenv("MANAGED_TAG", "xibo-sync")
    monkeypatch.setenv("DRY_RUN", "true" if dry_run else "false")
    monkeypatch.delenv("DISPLAY_GROUP_ID", raising=False)
    monkeypatch.delenv("TRIGGER_COLLECTNOW_ON_CHANGES", raising=False)


def _run_main(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    dry_run: bool = False,
    delete_layout_with_media: bool = True,
    client_factory=None,
    expected_exit_code: int = 0,
) -> FakeSyncClient:
    _configure(
        monkeypatch,
        tmp_path / "media",
        dry_run=dry_run,
        delete_layout_with_media=delete_layout_with_media,
    )
    cfg = load_config()
    fake_holder: dict[str, FakeSyncClient] = {}

    def factory(base_url: str, verify_tls: bool, timeout: int) -> FakeSyncClient:
        client = client_factory(base_url, verify_tls, timeout) if client_factory else FakeSyncClient(base_url, verify_tls, timeout)
        fake_holder["client"] = client
        return client

    monkeypatch.setattr(app, "XiboClient", factory)
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(app, "read_env_file", lambda path: {})
    monkeypatch.setattr(app, "write_env_file", lambda path, values: None)
    monkeypatch.setattr(app, "setup_logging", lambda *args: None)
    monkeypatch.setattr(app, "list_local_media", lambda *args: [])
    monkeypatch.setattr(app, "build_local_index", lambda *args: {})
    monkeypatch.setattr(
        app,
        "build_remote_index",
        lambda *args: {
            "media-owned": {"mediaId": "media-owned", "name": "owned.png", "tags": "xibo-sync"},
            "media-unrelated": {"mediaId": "media-unrelated", "name": "unrelated.png", "tags": "xibo-sync"},
        },
    )
    monkeypatch.setattr(app, "setup_logging", lambda *args: None)
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--delete", "--yes"] + (["--dry-run"] if dry_run else []))

    assert app.main() == expected_exit_code
    return fake_holder["client"]


def test_normal_delete_cleans_owned_layout_before_media_and_ignores_unrelated_layout(monkeypatch, tmp_path: Path) -> None:
    client = _run_main(monkeypatch, tmp_path)
    names = [name for name, _ in client.calls]

    assert names.index("list_layouts_by_ownership_tag") < names.index("cleanup_layout_for_media")
    assert names.index("cleanup_layout_for_media") < names.index("delete_media")
    assert [call for call in client.calls if call[0] == "list_layouts_by_ownership_tag"] == [
        ("list_layouts_by_ownership_tag", "xibo-sync-media:media-owned"),
        ("list_layouts_by_ownership_tag", "xibo-sync-media:media-unrelated"),
    ]
    assert ("cleanup_layout_for_media", ("owned-layout", "media-owned", "xibo-sync-media:media-owned", (), False)) in client.calls
    assert not any(call[0] == "cleanup_layout_for_media" and call[1][1] == "media-unrelated" for call in client.calls)
    assert [call[1][0] for call in client.calls if call[0] == "delete_media"] == ["media-owned", "media-unrelated"]


def test_delete_layout_with_media_disabled_deletes_media_without_layout_lookup(monkeypatch, tmp_path: Path) -> None:
    client = _run_main(monkeypatch, tmp_path, delete_layout_with_media=False)

    assert not any(name == "list_layouts_by_ownership_tag" for name, _ in client.calls)
    assert not any(name == "cleanup_layout_for_media" for name, _ in client.calls)
    assert [call[1][0] for call in client.calls if call[0] == "delete_media"] == [
        "media-owned", "media-unrelated"
    ]


@pytest.mark.parametrize(
    ("failure", "expected_message"),
    [
        ("Unassign layoutId=owned-layout from displayGroupId=group-1 failed", "Unassign layoutId=owned-layout"),
        ("Delete layoutId=owned-layout failed", "Delete layoutId=owned-layout"),
    ],
    ids=["display-group-unassignment", "layout-deletion"],
)
def test_cleanup_failure_prevents_media_deletion(
    monkeypatch, tmp_path: Path, failure: str, expected_message: str
) -> None:
    def failing_client(base_url: str, verify_tls: bool, timeout: int) -> FailingCleanupFakeSyncClient:
        return FailingCleanupFakeSyncClient(base_url, verify_tls, timeout, failure)

    client = _run_main(monkeypatch, tmp_path, client_factory=failing_client, expected_exit_code=2)

    assert ("cleanup_layout_for_media", ("owned-layout", "media-owned", "xibo-sync-media:media-owned", (), False)) in client.calls
    assert not any(name == "delete_media" for name, _ in client.calls)
    assert expected_message in failure


def test_externally_opened_tagged_draft_prevents_layout_and_media_deletion(monkeypatch, tmp_path: Path) -> None:
    client = _run_main(
        monkeypatch,
        tmp_path,
        client_factory=ExternallyOpenedDraftFakeSyncClient,
        expected_exit_code=2,
    )

    assert ("cleanup_layout_for_media", ("owned-layout", "media-owned", "xibo-sync-media:media-owned", (), False)) in client.calls
    assert not any(name in {"discard_layout_draft", "delete_layout", "delete_media"} for name, _ in client.calls)


def test_media_layout_final_publish_releases_tag_created_draft(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path / "media")
    cfg = load_config()
    cfg.delete_remote_not_local = False
    cfg.trigger_collectnow_on_changes = False
    fake = StatefulLayoutLifecycleFakeSyncClient("http://cms", False, 10)
    monkeypatch.setattr(app, "XiboClient", lambda *args: fake)
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(app, "read_env_file", lambda path: {})
    monkeypatch.setattr(app, "write_env_file", lambda path, values: None)
    monkeypatch.setattr(app, "setup_logging", lambda *args: None)
    monkeypatch.setattr(app, "list_local_media", lambda *args: [Path("new.png")])
    monkeypatch.setattr(app, "build_local_index", lambda *args: {"new.png": Path("new.png")})
    monkeypatch.setattr(app, "build_remote_index", lambda *args: {})
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])

    assert app.main() == 0
    names = [name for name, _ in fake.calls]
    assert names[names.index("publish_layout") + 1] == "get_layout_by_name"
    assert [name for name, _ in fake.calls[-3:]] == ["tag_layout", "publish_layout", "get_layout_by_name"]
    assert fake.layout_state == "published"


def test_media_layout_retries_publish_with_authoritative_id_after_draft_404(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path / "media")
    cfg = load_config()
    cfg.delete_remote_not_local = False
    cfg.trigger_collectnow_on_changes = False
    fake = DraftPublishRetryFakeSyncClient("http://cms", False, 10)
    monkeypatch.setattr(app, "XiboClient", lambda *args: fake)
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(app, "read_env_file", lambda path: {})
    monkeypatch.setattr(app, "write_env_file", lambda path, values: None)
    monkeypatch.setattr(app, "setup_logging", lambda *args: None)
    monkeypatch.setattr(app, "list_local_media", lambda *args: [Path("new.png")])
    monkeypatch.setattr(app, "build_local_index", lambda *args: {"new.png": Path("new.png")})
    monkeypatch.setattr(app, "build_remote_index", lambda *args: {})
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])

    assert app.main() == 0

    publish_calls = [call for call in fake.calls if call[0] == "publish_layout"]
    lookup_calls = [call for call in fake.calls if call[0] == "get_layout_by_name"]
    assert publish_calls == [
        ("publish_layout", ("draft-layout", False)),
        ("publish_layout", ("published-layout", False)),
        ("publish_layout", ("published-layout", False)),
    ]
    assert lookup_calls == [
        ("get_layout_by_name", "new.png fullscreen"),
        ("get_layout_by_name", "new.png fullscreen"),
        ("get_layout_by_name", "new.png fullscreen"),
    ]
    assert [name for name, _ in fake.calls[-3:]] == ["tag_layout", "publish_layout", "get_layout_by_name"]


def test_media_layout_retries_publish_after_tag_spawns_new_draft(monkeypatch, tmp_path: Path) -> None:
    _configure(monkeypatch, tmp_path / "media")
    cfg = load_config()
    cfg.delete_remote_not_local = False
    cfg.trigger_collectnow_on_changes = False
    fake = TagSpawnsNewDraftFakeSyncClient("http://cms", False, 10)
    monkeypatch.setattr(app, "XiboClient", lambda *args: fake)
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(app, "read_env_file", lambda path: {})
    monkeypatch.setattr(app, "write_env_file", lambda path, values: None)
    monkeypatch.setattr(app, "setup_logging", lambda *args: None)
    monkeypatch.setattr(app, "list_local_media", lambda *args: [Path("new.png")])
    monkeypatch.setattr(app, "build_local_index", lambda *args: {"new.png": Path("new.png")})
    monkeypatch.setattr(app, "build_remote_index", lambda *args: {})
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])

    assert app.main() == 0

    publish_calls = [call for call in fake.calls if call[0] == "publish_layout"]
    assert publish_calls == [
        ("publish_layout", ("draft-layout", False)),
        ("publish_layout", ("published-layout", False)),
        ("publish_layout", ("tag-draft-layout", False)),
    ]
    assert ("get_draft_layout_id", "published-layout") in fake.calls
    assert fake.pending_draft_id is None
    assert [name for name, _ in fake.calls[-6:]] == [
        "tag_layout",
        "publish_layout",
        "get_draft_layout_id",
        "get_layout_by_name",
        "publish_layout",
        "get_layout_by_name",
    ]


def test_media_layout_publish_404_on_already_published_layout_with_no_draft_succeeds(
    monkeypatch, tmp_path: Path
) -> None:
    _configure(monkeypatch, tmp_path / "media")
    cfg = load_config()
    cfg.delete_remote_not_local = False
    cfg.trigger_collectnow_on_changes = False
    fake = AlreadyPublishedNoDraftFakeSyncClient("http://cms", False, 10)
    monkeypatch.setattr(app, "XiboClient", lambda *args: fake)
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(app, "read_env_file", lambda path: {})
    monkeypatch.setattr(app, "write_env_file", lambda path, values: None)
    monkeypatch.setattr(app, "setup_logging", lambda *args: None)
    monkeypatch.setattr(app, "list_local_media", lambda *args: [Path("new.png")])
    monkeypatch.setattr(app, "build_local_index", lambda *args: {"new.png": Path("new.png")})
    monkeypatch.setattr(app, "build_remote_index", lambda *args: {})
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])

    assert app.main() == 0

    publish_calls = [call for call in fake.calls if call[0] == "publish_layout"]
    assert publish_calls == [
        ("publish_layout", ("draft-layout", False)),
        ("publish_layout", ("published-layout", False)),
    ]
    assert ("get_layout_by_id", "published-layout") in fake.calls
    assert any(name == "tag_layout" for name, _ in fake.calls)


@pytest.mark.parametrize(
    ("client_type", "expects_lookup"),
    [
        (Non404DraftPublishFailureFakeSyncClient, False),
        (SameIdDraftPublishRetryFakeSyncClient, True),
        (AmbiguousDraftPublishRetryFakeSyncClient, True),
    ],
    ids=["non-404", "same-id", "ambiguous-resolution"],
)
def test_media_layout_publish_recovery_does_not_retry_without_new_authoritative_id(
    monkeypatch, tmp_path: Path, client_type, expects_lookup: bool
) -> None:
    _configure(monkeypatch, tmp_path / "media")
    cfg = load_config()
    cfg.delete_remote_not_local = False
    cfg.trigger_collectnow_on_changes = False
    fake = client_type("http://cms", False, 10)
    monkeypatch.setattr(app, "XiboClient", lambda *args: fake)
    monkeypatch.setattr(app, "load_config", lambda: cfg)
    monkeypatch.setattr(app, "read_env_file", lambda path: {})
    monkeypatch.setattr(app, "write_env_file", lambda path, values: None)
    monkeypatch.setattr(app, "setup_logging", lambda *args: None)
    monkeypatch.setattr(app, "list_local_media", lambda *args: [Path("new.png")])
    monkeypatch.setattr(app, "build_local_index", lambda *args: {"new.png": Path("new.png")})
    monkeypatch.setattr(app, "build_remote_index", lambda *args: {})
    monkeypatch.setattr("sys.argv", ["sync_xibo.py", "--yes"])

    assert app.main() == 2

    publish_calls = [call for call in fake.calls if call[0] == "publish_layout"]
    assert publish_calls == [("publish_layout", ("draft-layout", False))]
    assert any(call[0] == "get_layout_by_name" for call in fake.calls) is expects_lookup
    assert not any(name == "tag_layout" for name, _ in fake.calls)


def test_dry_run_deletion_calls_planning_helpers_without_mutating_transport(monkeypatch, tmp_path: Path) -> None:
    client = _run_main(monkeypatch, tmp_path, dry_run=True)

    assert ("cleanup_layout_for_media", ("owned-layout", "media-owned", "xibo-sync-media:media-owned", (), True)) in client.calls
    assert [call[1][1] for call in client.calls if call[0] == "delete_media"] == [True, True]
    assert not any(name == "publish_layout" for name, _ in client.calls)