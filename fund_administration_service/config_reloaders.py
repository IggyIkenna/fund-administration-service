"""Domain config hot-reload wiring for fund-administration-service.

Typed wrapper around UTL's ``DomainConfigReloader``. Intentionally small — the
fund-administration-service keeps its own domain config (grace-period defaults,
NAV cadence, treasury wallet id) on the typed ``FundAdministrationServiceConfig``
and simply hot-reloads fund-administration-shared venues/instruments from the
shared config bucket so the ``CapitalRouter`` uses up-to-date venue metadata
when driving transfers.

Pattern required by STEP 5.34 in base-service.sh: parameter is a typed
``FundAdministrationServiceConfig`` — never ``object`` and never untyped
attribute-name lookups.
"""

from __future__ import annotations

import logging

from unified_trading_library import (
    DomainConfigReloader,
    VenueDomainConfig,
    log_event,
)

from fund_administration_service.config import FundAdministrationServiceConfig

logger = logging.getLogger(__name__)

_venue_reloader: DomainConfigReloader[VenueDomainConfig] | None = None
_active_venues: VenueDomainConfig | None = None


def get_active_venues() -> VenueDomainConfig | None:
    """Return the latest venues domain config snapshot, or ``None`` if unloaded."""

    return _active_venues


def _on_venues_reload(config: VenueDomainConfig) -> None:
    global _active_venues
    _active_venues = config  # atomic single-assignment swap
    logger.info(
        "Venues domain config reloaded: %d enabled venues",
        len(config.enabled_venues),
    )
    log_event(
        "CONFIG_CHANGED",
        details={
            "domain": "venues",
            "service": "fund-administration-service",
            "enabled_venues_count": len(config.enabled_venues),
        },
    )


def start_fund_administration_config_reloaders(
    service_config: FundAdministrationServiceConfig,
) -> None:
    """Start domain config reloaders. Call from service bootstrap on startup."""

    global _venue_reloader

    config_store_bucket: str = service_config.config_store_bucket
    project_id: str | None = service_config.gcp_project_id

    if not config_store_bucket:
        logger.info("CONFIG_STORE_BUCKET not set — fund-administration hot-reload disabled")
        return

    _venue_reloader = DomainConfigReloader(
        domain="venues",
        config_class=VenueDomainConfig,
        config_bucket=config_store_bucket,
        project_id=project_id,
    )
    _venue_reloader.on_reload(_on_venues_reload)
    _venue_reloader.start_watching()

    logger.info("fund-administration config reloaders started: venues")


def stop_fund_administration_config_reloaders() -> None:
    """Stop domain config reloaders. Call from shutdown."""

    global _venue_reloader
    if _venue_reloader is not None:
        _venue_reloader.stop_watching()
        _venue_reloader = None
    logger.info("fund-administration config reloaders stopped")
