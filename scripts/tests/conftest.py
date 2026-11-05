"""Shared pytest configuration for scripts/tests/.

Auto-marks every test under tests/integration/ with the ``integration`` marker so
``pytest -m "not integration"`` runs only the fast, offline unit suite without needing
per-test decorators.
"""

import pytest
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT / "services"))


@pytest.fixture(autouse=True)
def isolated_application_configuration(monkeypatch, tmp_path):
    import xibo_sync.app as app
    from xibo_sync.env_io import CREDENTIAL_KEYS, SETTING_KEYS

    for key in CREDENTIAL_KEYS | SETTING_KEYS:
        monkeypatch.delenv(key, raising=False)
    original_main = app.main

    def isolated_main(*, scripts_dir=None):
        return original_main(scripts_dir=tmp_path if scripts_dir is None else scripts_dir)

    monkeypatch.setattr(app, "main", isolated_main)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.nodeid.replace("\\", "/").split("/"):
            item.add_marker(pytest.mark.integration)
