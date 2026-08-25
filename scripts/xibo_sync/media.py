"""Local media scanning and remote index building helpers for sync comparisons."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Dict, List, Tuple


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    """Return the SHA-256 hex digest for a file.

    Args:
        path: File to hash.
        chunk_size: Number of bytes to read per iteration.

    Returns:
        Hexadecimal SHA-256 digest.
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def list_local_media(media_dir: Path, extensions: Tuple[str, ...]) -> List[Path]:
    """Return all local media files under the configured directory.

    Args:
        media_dir: Root directory to scan.
        extensions: Allowed file extensions, including the leading dot.

    Returns:
        Sorted list of matching media files.

    Raises:
        FileNotFoundError: Raised when the media directory does not exist.
    """
    if not media_dir.exists():
        raise FileNotFoundError(f"Local media directory does not exist: {media_dir.resolve()}")

    files: List[Path] = []
    for p in media_dir.rglob("*"):
        if p.is_file() and p.suffix.lower() in extensions:
            files.append(p)

    return sorted(files)


def build_local_index(files: List[Path], mode: str, hash_tag_prefix: str) -> Dict[str, Path]:
    """Build the local comparison index using filename or hash keys.

    Args:
        files: Local media files to index.
        mode: Comparison mode, either ``filename`` or ``hash``.
        hash_tag_prefix: Prefix used when generating hash keys.

    Returns:
        Mapping of comparison keys to local file paths.
    """
    idx: Dict[str, Path] = {}
    if mode == "filename":
        for f in files:
            idx[f.name] = f
    else:
        for f in files:
            idx[f"{hash_tag_prefix}{sha256_file(f)}"] = f
    return idx


def build_remote_index(items: List[dict], mode: str, hash_tag_prefix: str) -> Dict[str, dict]:
    """Build the remote comparison index using filename or hash tags.

    Args:
        items: Remote media items returned by the CMS.
        mode: Comparison mode, either ``filename`` or ``hash``.
        hash_tag_prefix: Prefix used to identify hash tags.

    Returns:
        Mapping of comparison keys to remote media dictionaries.
    """
    idx: Dict[str, dict] = {}
    for it in items:
        if mode == "filename":
            key = it.get("name") or it.get("fileName") or it.get("originalFileName")
            if key:
                idx[str(key)] = it
        else:
            tags = it.get("tags")
            found = None

            if isinstance(tags, str):
                for t in [x.strip() for x in tags.split(",")]:
                    if t.startswith(hash_tag_prefix):
                        found = t
                        break
            elif isinstance(tags, list):
                for t in tags:
                    if isinstance(t, str) and t.startswith(hash_tag_prefix):
                        found = t
                        break
                    if isinstance(t, dict):
                        tag_str = t.get("tag") or t.get("name")
                        if isinstance(tag_str, str) and tag_str.startswith(hash_tag_prefix):
                            found = tag_str
                            break

            if found:
                idx[found] = it

    return idx
