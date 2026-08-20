"""Unit tests for scripts/xibo_sync/media.py."""

import hashlib
from pathlib import Path

import pytest

from xibo_sync.media import build_local_index, build_remote_index, list_local_media, sha256_file


def test_sha256_file_matches_hashlib(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.txt"
    file_path.write_bytes(b"hello world")

    assert sha256_file(file_path) == hashlib.sha256(b"hello world").hexdigest()


def test_list_local_media_raises_for_missing_directory(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        list_local_media(tmp_path / "missing", (".jpg",))


def test_list_local_media_filters_by_extension(tmp_path: Path) -> None:
    (tmp_path / "photo.JPG").write_bytes(b"1")
    (tmp_path / "note.txt").write_bytes(b"2")
    subdir = tmp_path / "sub"
    subdir.mkdir()
    (subdir / "clip.mp4").write_bytes(b"3")

    files = list_local_media(tmp_path, (".jpg", ".mp4"))

    assert sorted(f.name for f in files) == ["clip.mp4", "photo.JPG"]


def test_build_local_index_filename_mode(tmp_path: Path) -> None:
    file_path = tmp_path / "photo.jpg"
    file_path.write_bytes(b"data")

    index = build_local_index([file_path], "filename", "sha256:")

    assert index == {"photo.jpg": file_path}


def test_build_local_index_hash_mode(tmp_path: Path) -> None:
    file_path = tmp_path / "photo.jpg"
    file_path.write_bytes(b"data")
    expected_hash = hashlib.sha256(b"data").hexdigest()

    index = build_local_index([file_path], "hash", "sha256:")

    assert index == {f"sha256:{expected_hash}": file_path}


def test_build_remote_index_filename_mode() -> None:
    items = [{"name": "photo.jpg"}, {"fileName": "clip.mp4"}, {"id": 1}]

    index = build_remote_index(items, "filename", "sha256:")

    assert set(index.keys()) == {"photo.jpg", "clip.mp4"}


def test_build_remote_index_hash_mode_string_tags() -> None:
    items = [{"tags": "foo,sha256:abc123"}]

    index = build_remote_index(items, "hash", "sha256:")

    assert list(index.keys()) == ["sha256:abc123"]


def test_build_remote_index_hash_mode_list_of_string_tags() -> None:
    items = [{"tags": ["foo", "sha256:abc123"]}]

    index = build_remote_index(items, "hash", "sha256:")

    assert list(index.keys()) == ["sha256:abc123"]


def test_build_remote_index_hash_mode_list_of_dict_tags() -> None:
    items = [{"tags": [{"tag": "sha256:abc123"}]}]

    index = build_remote_index(items, "hash", "sha256:")

    assert list(index.keys()) == ["sha256:abc123"]


def test_build_remote_index_hash_mode_ignores_items_without_matching_tag() -> None:
    items = [{"tags": "foo,bar"}]

    index = build_remote_index(items, "hash", "sha256:")

    assert index == {}
