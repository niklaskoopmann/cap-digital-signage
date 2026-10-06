"""Unit tests for the Microsoft Graph transport client."""

from pathlib import Path

import pytest
import requests

from xibo_sync.graph_client import GraphClient


class FakeResponse:
    def __init__(self, payload=None, *, status_code=200, content=(), text=""):
        self.payload = payload or {}
        self.status_code = status_code
        self.content = content
        self.text = text

    def json(self):
        return self.payload

    def iter_content(self, chunk_size):
        for chunk in self.content:
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk


class FakeMsalApp:
    def __init__(self, result):
        self.result = result
        self.calls = []

    def acquire_token_for_client(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_client(session, token="graph-token"):
    app = FakeMsalApp({"access_token": token})
    client = GraphClient("tenant", "client", "client-secret", "https://graph.test/v1.0", 17,
                         msal_app=app, session=session)
    client.authenticate()
    return client, app


def test_authenticate_uses_default_scope_and_does_not_leak_secret() -> None:
    app = FakeMsalApp({"error": "invalid_client", "error_description": "bad credentials"})
    client = GraphClient("tenant", "client", "not-for-logs", "https://graph.test/v1.0", 30,
                         msal_app=app, session=FakeSession([]))

    with pytest.raises(RuntimeError, match="bad credentials") as error:
        client.authenticate()

    assert app.calls == [{"scopes": ["https://graph.microsoft.com/.default"]}]
    assert "invalid_client" in str(error.value)
    assert "not-for-logs" not in str(error.value)


def test_get_paged_follows_next_link_and_sends_timeout_once_with_params() -> None:
    session = FakeSession([
        FakeResponse({"@odata.context": "context", "value": [{"id": "1"}],
                     "@odata.nextLink": "https://graph.test/next"}),
        FakeResponse({"value": [{"id": "2"}]}),
    ])
    client, app = make_client(session)

    context, items = client.get_paged("/events", params={"top": 1})

    assert context == "context"
    assert items == [{"id": "1"}, {"id": "2"}]
    assert session.calls[0][0] == "https://graph.test/v1.0/events"
    assert session.calls[0][1]["params"] == {"top": 1}
    assert session.calls[1][1]["params"] is None
    assert all(call[1]["timeout"] == 17 for call in session.calls)
    assert all(call[1]["headers"] == {"Authorization": "Bearer graph-token"} for call in session.calls)
    assert app.calls


def test_get_paged_reports_non_success_status() -> None:
    client, _app = make_client(FakeSession([FakeResponse(status_code=403, text="forbidden")]))

    with pytest.raises(RuntimeError, match="403") as error:
        client.get_paged("/events")

    assert "forbidden" in str(error.value)


def test_download_url_is_unauthenticated_and_writes_bytes(tmp_path: Path) -> None:
    session = FakeSession([FakeResponse(content=[b"image", b" bytes"])])
    client, _app = make_client(session)
    dest = tmp_path / "media.png"

    client.download({"id": "item", "name": "media.png", "@microsoft.graph.downloadUrl": "https://download.test/token"}, "site", dest)

    assert dest.read_bytes() == b"image bytes"
    assert session.calls[0][1]["headers"] == {}
    assert session.calls[0][1]["timeout"] == 17


def test_download_content_endpoint_uses_bearer_token(tmp_path: Path) -> None:
    session = FakeSession([FakeResponse(content=[b"payload"])])
    client, _app = make_client(session)
    dest = tmp_path / "media.png"

    client.download({"id": "item id", "name": "media.png"}, "site/id", dest)

    assert session.calls[0][0] == "https://graph.test/v1.0/sites/site%2Fid/drive/items/item%20id/content"
    assert session.calls[0][1]["headers"] == {"Authorization": "Bearer graph-token"}
    assert session.calls[0][1]["timeout"] == 17
    assert dest.read_bytes() == b"payload"


def test_download_failure_removes_partial_file_and_sanitizes_url(tmp_path: Path) -> None:
    secret_url = "https://download.test/preauthenticated-secret"
    session = FakeSession([requests.ConnectionError(secret_url)])
    client, _app = make_client(session)
    dest = tmp_path / "media.png"

    with pytest.raises(RuntimeError, match="media.png") as error:
        client.download({"id": "item", "name": "media.png", "@microsoft.graph.downloadUrl": secret_url}, "site", dest)

    assert secret_url not in str(error.value)
    assert str(error.value).endswith(": ConnectionError")
    assert not dest.exists()
    assert not dest.with_suffix(".png.part").exists()


def test_download_failure_reports_http_status(tmp_path: Path) -> None:
    client, _app = make_client(FakeSession([FakeResponse(status_code=404)]))

    with pytest.raises(RuntimeError, match=r"media\.png \(id item\): HTTP status 404"):
        client.download({"id": "item", "name": "media.png"}, "site", tmp_path / "media.png")


def test_midstream_download_failure_removes_partial_file(tmp_path: Path) -> None:
    secret_url = "https://download.test/preauthenticated-secret"
    session = FakeSession([
        FakeResponse(content=[b"partial", requests.ConnectionError(secret_url)])
    ])
    client, _app = make_client(session)
    dest = tmp_path / "media.png"

    with pytest.raises(RuntimeError, match="media.png") as error:
        client.download(
            {"id": "item", "name": "media.png", "@microsoft.graph.downloadUrl": secret_url},
            "site",
            dest,
        )

    assert secret_url not in str(error.value)
    assert not dest.exists()
    assert not dest.with_suffix(".png.part").exists()