"""Thin launcher that keeps sync_xibo.py as the single run target."""

from __future__ import annotations

from xibo_sync.app import main


if __name__ == "__main__":
    raise SystemExit(main())
