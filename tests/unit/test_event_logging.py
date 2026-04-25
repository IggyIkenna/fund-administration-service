"""Unit tests for standardized event logging compliance.

Verifies required events are present in service source code.
"""

import os
import re
from pathlib import Path

import pytest

# fund-administration-service is a state-machine service, not a data
# pipeline. Common lifecycle events (STARTED / STOPPED / FAILED) are emitted
# by `ServiceBootstrap` in UTL, NOT by the service itself (per STEP 5.61 in
# unified-trading-pm/cursor-configs/SUB_AGENT_MANDATORY_RULES.md — "Services
# do NOT emit these manually"). The service-specific events are the
# subscription + redemption + allocation lifecycle events registered in UTL
# `STANDARD_LIFECYCLE_EVENTS` (2026-04-20) — those ARE emitted from this
# service's source and therefore appear in the regex scan.
REQUIRED_COMMON_EVENTS: list[str] = []

SERVICE_SPECIFIC_EVENTS: dict[str, list[str]] = {
    "fund-administration-service": [
        "SUBSCRIPTION_REQUESTED",
        "SUBSCRIPTION_APPROVED",
        "SUBSCRIPTION_REJECTED",
        "SUBSCRIPTION_SETTLED",
        "REDEMPTION_REQUESTED",
        "REDEMPTION_APPROVED",
        "REDEMPTION_REJECTED",
        "REDEMPTION_PROCESSED",
        "REDEMPTION_SETTLED",
        "FUND_ALLOCATION_REBALANCED",
    ],
}


def get_service_name() -> str:
    """Detect service name from current directory."""
    return Path.cwd().name


def find_python_files(service_dir: Path) -> list[Path]:
    exclude = {"tests", ".venv", "venv", "__pycache__", ".git", "examples"}
    result = []
    for root, dirs, files in os.walk(service_dir, followlinks=False):
        dirs[:] = [d for d in dirs if d not in exclude]
        for f in files:
            if f.endswith(".py"):
                result.append(Path(root) / f)
    return result


@pytest.fixture
def all_event_markers() -> set[str]:
    """Discover event markers in service source code.

    Matches any emit-style helper call — ``log_event``, ``emit_*_event`` —
    with either a string literal or an imported constant as the first
    argument. This service wraps ``log_event`` behind
    ``emit_fund_admin_event(EVENT_NAME, ...)``, so the regex covers both
    patterns.
    """
    events: set[str] = set()
    emit_call_pattern = re.compile(
        r"(?:log_event|emit_[a-z_]*event)\(\s*(?:[\"']([A-Z][A-Z0-9_]*)[\"']|([A-Z][A-Z0-9_]*))",
        re.MULTILINE,
    )
    for py in find_python_files(Path.cwd()):
        text = py.read_text()
        for match in emit_call_pattern.finditer(text):
            name = match.group(1) or match.group(2)
            if name:
                events.add(name)
    return events


def test_common_events_exist(all_event_markers: set[str]) -> None:
    """Verify required common lifecycle events are present."""
    if not all_event_markers:
        pytest.skip("No event markers found")
    required = set(REQUIRED_COMMON_EVENTS)
    missing = required - all_event_markers
    assert not missing, f"Missing required common events: {sorted(missing)}"


def test_service_specific_events_exist(all_event_markers: set[str]) -> None:
    """Verify service-specific events are present."""
    name = get_service_name()
    if name not in SERVICE_SPECIFIC_EVENTS:
        pytest.skip(f"No service-specific events for {name}")
    required = set(SERVICE_SPECIFIC_EVENTS[name])
    missing = required - all_event_markers
    assert not missing, f"Missing service-specific events for {name}: {sorted(missing)}"


def test_event_helper_imported(all_event_markers: set[str]) -> None:
    """Verify log_event is imported when events are used - Pattern B only."""
    if not all_event_markers:
        pytest.skip("No event markers found")
    found = False
    for py in find_python_files(Path.cwd()):
        text = py.read_text()
        if "from unified_trading_library import log_event" in text:
            found = True
            break
    assert found, "log_event not imported. Add: from unified_trading_library import log_event"
