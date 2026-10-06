"""Thin launcher that keeps sync_xibo.py as the single run target."""

from __future__ import annotations

import truststore

# Trust OS-installed CAs (e.g. corporate TLS inspection roots) instead of only certifi.
truststore.inject_into_ssl()

from xibo_sync.app import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
