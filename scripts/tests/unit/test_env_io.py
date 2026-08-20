"""Unit tests for scripts/xibo_sync/env_io.py."""

from pathlib import Path

from xibo_sync.env_io import read_env_file, write_env_file


def test_read_env_file_returns_empty_dict_when_missing(tmp_path: Path) -> None:
    assert read_env_file(tmp_path / "missing.env") == {}


def test_read_env_file_parses_values_and_skips_comments(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# a full-line comment",
                "",
                "CMS_BASE_URL=http://192.168.1.1",
                "DRY_RUN=false  # inline comment",
                "not-a-kv-line",
            ]
        ),
        encoding="utf-8",
    )

    values = read_env_file(env_path)

    assert values == {
        "CMS_BASE_URL": "http://192.168.1.1",
        "DRY_RUN": "false",
    }


def test_write_env_file_creates_new_file_sorted(tmp_path: Path) -> None:
    env_path = tmp_path / "new.env"

    write_env_file(env_path, {"B_KEY": "2", "A_KEY": "1"})

    assert env_path.read_text(encoding="utf-8").splitlines() == ["A_KEY=1", "B_KEY=2"]


def test_write_env_file_preserves_comments_and_updates_known_keys(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        "\n".join(
            [
                "# keep this comment",
                "CMS_BASE_URL=http://old",
                "UNRELATED=stay",
            ]
        ),
        encoding="utf-8",
    )

    write_env_file(env_path, {"CMS_BASE_URL": "http://new", "NEW_KEY": "value"})

    lines = env_path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "# keep this comment"
    assert "CMS_BASE_URL=http://new" in lines
    assert "UNRELATED=stay" in lines
    assert "NEW_KEY=value" in lines
