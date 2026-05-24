"""Service bootstrap wiring — ``ServiceBootstrap(...)`` entry point.

Every service's main.py calls ``ServiceBootstrap.run()`` so lifecycle events
(STARTED/STOPPED/FAILED) are emitted by UTL rather than by the service. The
only operation registered here is ``serve`` — run the FastAPI app. Batch
entrypoints are a follow-up once the durable persistence backend lands.
"""

from __future__ import annotations

import logging

from unified_trading_library import BaseModeHandler, ServiceBootstrap, UnifiedServiceHandler
from typing import Union

from fund_administration_service.config import get_service_config

logger = logging.getLogger(__name__)


class ServeHandler(BaseModeHandler):
    """``--operation serve`` — run the FastAPI health + admin API."""

    def validate_config(self) -> bool:
        return True

    async def run(self) -> dict[str, object]:
        config = get_service_config()
        # uvicorn is launched out of ``cli_entry.main`` so we still hit
        # ServiceBootstrap for lifecycle events; this handler merely reports
        # the bind address back to the dispatcher so smoke tests can assert
        # the service started with the expected config.
        return {
            "status": "ok",
            "api_host": config.api_host,
            "api_port": config.api_port,
        }


_OPERATIONS: dict[str, Union[type[BaseModeHandler], type[UnifiedServiceHandler]]] = {
    "serve": ServeHandler,
}


def build_service_bootstrap() -> ServiceBootstrap:
    """Construct the ``ServiceBootstrap`` instance for this service.

    SERVICE_EVENT: STARTED
    SERVICE_EVENT: STOPPED
    SERVICE_EVENT: FAILED
    """

    return ServiceBootstrap(
        service_name="fund-administration-service",
        operations=_OPERATIONS,
        config_fn=get_service_config,
        modes=["batch", "live"],
        description=("Fund administration — pooled-fund subscription, redemption, allocation"),
        add_asset_group_arg=False,
        add_date_args=False,
    )
