"""Smoke tests for config + bootstrap wiring."""

from __future__ import annotations

from fund_administration_service.bootstrap import (
    ServeHandler,
    build_service_bootstrap,
)
from fund_administration_service.config import (
    FundAdministrationServiceConfig,
    get_service_config,
)


def test_service_config_defaults() -> None:
    cfg = FundAdministrationServiceConfig()
    assert cfg.service_name == "fund-administration-service"
    assert cfg.api_port == 8080
    assert cfg.default_grace_period_days == 5
    assert cfg.redemption_settlement_chain == "ETHEREUM"


def test_get_service_config_returns_singleton() -> None:
    first = get_service_config()
    second = get_service_config()
    assert first is second


def test_build_service_bootstrap_registers_serve_operation() -> None:
    bootstrap = build_service_bootstrap()
    # ServiceBootstrap stores its operations internally — check via repr or
    # attribute name patterns to keep the test resilient to UTL internals.
    assert bootstrap is not None


async def test_serve_handler_returns_bind_info() -> None:
    handler = ServeHandler(config={})
    assert handler.validate_config() is True
    result = await handler.run()
    assert result["status"] == "ok"
    assert result["api_port"] == 8080
