"""Integration smoke — end-to-end lifecycle through the FastAPI app.

Not executed by the default QG (RUN_INTEGRATION=false) but kept in-repo for
CI integration runs and local full-stack validation. Duplicates the end-to-end
coverage of tests/unit/test_api_end_to_end.py at the integration-marker level
so future SIT runs can assert the same wire behaviour.
"""

from __future__ import annotations

import pytest

from tests.unit.test_api_end_to_end import (
    test_full_lifecycle_loop_emits_all_events_in_order as _e2e_loop,
)
from tests.unit.test_api_end_to_end import (
    test_health_endpoint_returns_ok as _health_ok,
)


@pytest.mark.integration
def test_integration_full_lifecycle_loop() -> None:
    _e2e_loop()


@pytest.mark.integration
def test_integration_health_endpoint() -> None:
    _health_ok()
