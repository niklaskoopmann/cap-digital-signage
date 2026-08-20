"""Shared pytest configuration for scripts/tests/.

Auto-marks every test under tests/integration/ with the ``integration`` marker so
``pytest -m "not integration"`` runs only the fast, offline unit suite without needing
per-test decorators.
"""

import pytest


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if "integration" in item.nodeid.replace("\\", "/").split("/"):
            item.add_marker(pytest.mark.integration)
