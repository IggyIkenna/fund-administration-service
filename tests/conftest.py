"""Shared pytest fixtures for fund-administration-service."""

from __future__ import annotations

import os

os.environ.setdefault("CLOUD_PROVIDER", "local")
os.environ.setdefault("CLOUD_MOCK_MODE", "true")
os.environ.setdefault("GCP_PROJECT_ID", "test-project")

# Initialise UTL event logging in local-mode for the whole test session.
# log_event() with mode="local" + sink=None simply logs to stdout — no network
# traffic and no event-sink plumbing required for unit tests.
from unified_trading_library import setup_events

setup_events(service_name="fund-administration-service", mode="local")
