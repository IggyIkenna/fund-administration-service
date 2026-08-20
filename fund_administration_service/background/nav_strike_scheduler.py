"""NAV strike scheduler — triggers ``FundNAVSnapshot`` capture at publish cadence.

In the initial scaffold the scheduler is a thin wrapper around the injected
``NavProvider`` — it exposes ``tick()`` so tests and orchestrators can advance
the clock deterministically. The real wall-clock loop + pub-sub trigger lives
in the live-mode handler that runs alongside FastAPI; the scheduler class
itself stays pure so test coverage is cheap.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from unified_api_contracts import FundNAVSnapshot

from fund_administration_service.config import FundAdministrationServiceConfig
from fund_administration_service.subscription import NavProvider

logger = logging.getLogger(__name__)


class NAVStrikeScheduler:
    """Fires NAV strikes on a fixed wall-clock cadence."""

    def __init__(
        self,
        service_config: FundAdministrationServiceConfig,
        nav_provider: NavProvider,
    ) -> None:
        self._cadence_seconds = service_config.nav_publish_cadence_seconds
        self._nav_provider = nav_provider

    @property
    def cadence_seconds(self) -> int:
        """Seconds between scheduled NAV strikes."""

        return self._cadence_seconds

    async def run_forever(
        self,
        interval_seconds: int,
        fund_share_classes: Sequence[tuple[str, str]] = (),
    ) -> None:
        """Wall-clock loop — calls ``tick()`` for every ``(fund_id, share_class)``
        pair in ``fund_share_classes`` every ``interval_seconds``, forever.

        ``fund_share_classes`` is the platform's currently-registered set of
        active fund/share-class pairs to strike each cadence tick — empty by
        default (fund-administration-service has no fund-registry API yet;
        out of this scaffold's scope). Started from the same FastAPI
        startup/lifespan hook as ``GracePeriodHandler.run_forever()``. Each
        pair is shard-isolated — a ``tick()`` failure for one pair is logged
        and never blocks the rest.
        """

        while True:
            await asyncio.sleep(interval_seconds)
            for fund_id, share_class in fund_share_classes:
                try:
                    self.tick(fund_id, share_class)
                except Exception:  # noqa: broad-except — shard-level isolation: one pair's failure must never block the rest
                    logger.exception(
                        "NAVStrikeScheduler.tick failed fund_id=%s share_class=%s",
                        fund_id,
                        share_class,
                    )

    def tick(self, fund_id: str, share_class: str) -> FundNAVSnapshot | None:
        """Pull the most recent NAV snapshot for ``(fund_id, share_class)``.

        Returns ``None`` if no snapshot is available yet (scheduler skips until
        the NAV source populates). Real loop: an ``asyncio.sleep`` driven loop
        in the live-mode handler invokes ``tick`` every ``cadence_seconds``.
        """

        snapshot = self._nav_provider.latest_snapshot(fund_id, share_class)
        if snapshot is None:
            logger.info(
                "NAVStrikeScheduler.tick: no snapshot yet for fund=%s share_class=%s",
                fund_id,
                share_class,
            )
        return snapshot
