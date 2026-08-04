"""Minimal .env read/write helpers that preserve comments where possible."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional


def _strip_inline_comment(value: str) -> str:
    """Strip inline comments of the form ``VALUE  # comment``.

    Args:
        value: Raw value text from the right-hand side of a ``KEY=VALUE`` pair.

    Returns:
        Value text with a trailing inline comment removed when present.
    """
    for i in range(len(value) - 1):
        if value[i].isspace() and value[i + 1] == "#":
            return value[:i].strip()
    return value.strip()


def read_env_file(env_path: Path) -> Dict[str, str]:
    """Read a simple ``KEY=VALUE`` .env file.

    Args:
        env_path: Path to the environment file.

    Returns:
        Mapping of parsed environment keys to values.
    """
    data: Dict[str, str] = {}
    if not env_path.exists():
        return data

    for line in env_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        data[k.strip()] = _strip_inline_comment(v)
    return data


def write_env_file(env_path: Path, values: Dict[str, str], original_lines: Optional[List[str]] = None) -> None:
    """Write the .env file while preserving existing comments where possible.

    Args:
        env_path: Destination path for the environment file.
        values: Key/value pairs to write back to the file.
        original_lines: Optional original file contents used to preserve layout.
    """
    if original_lines is None and env_path.exists():
        original_lines = env_path.read_text(encoding="utf-8").splitlines()
    if original_lines is None:
        original_lines = []

    known_keys = set(values.keys())
    out_lines: List[str] = []
    seen: set[str] = set()

    for line in original_lines:
        if "=" in line and not line.strip().startswith("#"):
            k = line.split("=", 1)[0].strip()
            if k in known_keys:
                out_lines.append(f"{k}={values[k]}")
                seen.add(k)
            else:
                out_lines.append(line)
        else:
            out_lines.append(line)

    for k in sorted(known_keys):
        if k not in seen:
            out_lines.append(f"{k}={values[k]}")

    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
