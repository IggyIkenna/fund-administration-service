"""Typed configuration for fund-administration-service.

Extends ``UnifiedCloudConfig`` for cloud-agnostic infrastructure. Never read raw
``os.environ`` here — every field is a typed, validated ``Field``.
"""

from __future__ import annotations

from pydantic import AliasChoices, Field
from unified_trading_library import UnifiedCloudConfig


class FundAdministrationServiceConfig(UnifiedCloudConfig):
    """Configuration for fund-administration-service.

    Inheritance chain: ``BaseConfig`` -> ``UnifiedCloudConfig`` ->
    ``FundAdministrationServiceConfig``.
    """

    service_name: str = Field(
        default="fund-administration-service",
        validation_alias=AliasChoices("SERVICE_NAME", "service_name"),
        description="Service name for logging and identification",
    )

    api_host: str = Field(
        # Loopback by default — container deployments override via the
        # ``API_HOST`` env var to bind all interfaces (``0.0.0.0``).
        # Avoids Bandit B104 on the default value.
        default="127.0.0.1",
        validation_alias=AliasChoices("API_HOST", "api_host"),
        description="Host for the FastAPI server",
    )
    api_port: int = Field(
        default=8080,
        validation_alias=AliasChoices("API_PORT", "api_port"),
        description="Port for the FastAPI server",
    )

    default_grace_period_days: int = Field(
        default=5,
        validation_alias=AliasChoices("DEFAULT_GRACE_PERIOD_DAYS", "default_grace_period_days"),
        description="Default redemption grace period (business days)",
    )

    redemption_cadence_seconds: int = Field(
        default=28800,
        validation_alias=AliasChoices("REDEMPTION_CADENCE_SECONDS", "redemption_cadence_seconds"),
        description="Grace-period watchdog polling interval in seconds (default 8h)",
    )

    nav_publish_cadence_seconds: int = Field(
        default=86400,
        validation_alias=AliasChoices("NAV_PUBLISH_CADENCE_SECONDS", "nav_publish_cadence_seconds"),
        description="NAV strike cadence — scheduler interval in seconds",
    )

    treasury_wallet_id: str = Field(
        default="treasury-default",
        validation_alias=AliasChoices("TREASURY_WALLET_ID", "treasury_wallet_id"),
        description="Custodian wallet id for the fund treasury",
    )

    redemption_settlement_chain: str = Field(
        default="ETHEREUM",
        validation_alias=AliasChoices("REDEMPTION_SETTLEMENT_CHAIN", "redemption_settlement_chain"),
        description="Default chain used when withdrawing to allocator on-chain destination",
    )

    config_store_bucket: str = Field(
        default="",
        validation_alias=AliasChoices("CONFIG_STORE_BUCKET", "config_store_bucket"),
        description=(
            "Cloud storage bucket holding the venues domain config store — consumed by "
            "config_reloaders.start_fund_administration_config_reloaders() for hot-reload. "
            "Empty (default) disables hot-reload, matching UnifiedCloudConfig's own lack of "
            "this field (each service declares it locally; see execution-service / "
            "alerting-service / client-reporting-api config.py for the same pattern)."
        ),
    )


_service_config: FundAdministrationServiceConfig | None = None


def get_service_config() -> FundAdministrationServiceConfig:
    """Return the process-wide ``FundAdministrationServiceConfig`` singleton."""

    global _service_config
    if _service_config is None:
        _service_config = FundAdministrationServiceConfig()
    return _service_config
