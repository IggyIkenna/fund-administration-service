"""Grace-period watchdog for APPROVED redemptions.

Polls the persistence store for redemptions in state APPROVED and, for each
whose grace-period has expired, drives the PROCESSED transition using a
settlement NAV and a ``FeeStructure``. Shard-level failure isolation — a
processing error on one redemption does NOT raise across the iteration
boundary; the individual failure is logged + event-emitted and the handler
moves to the next redemption.

This is the simple wall-clock variant: grace-period expiry = ``requested_timestamp
+ grace_period_days * 86400s``. The business-day calendar calculation is a
follow-up once the execution calendar service lands.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from unified_api_contracts.internal import (
    AllocatorRedemption,
    FeeStructure,
    RedemptionStatus,
)
from unified_trading_library import REDEMPTION_PROCESSED, REDEMPTION_SETTLED

from fund_administration_service.allocation.transfer_protocol import (
    FundTransferContext,
    TransferAdapter,
)
from fund_administration_service.config import FundAdministrationServiceConfig
from fund_administration_service.events import emit_fund_admin_event
from fund_administration_service.persistence import PersistenceStore
from fund_administration_service.redemption import (
    process_redemption,
    settle_redemption,
)
from fund_administration_service.subscription import NavProvider

logger = logging.getLogger(__name__)


class GracePeriodHandler:
    """Drive APPROVED redemptions to PROCESSED once the grace period expires."""

    def __init__(
        self,
        service_config: FundAdministrationServiceConfig,
        store: PersistenceStore,
        nav_provider: NavProvider,
        fee_structure_for_fund: dict[str, FeeStructure],
        transfer_adapter: TransferAdapter,
    ) -> None:
        self._config = service_config
        self._store = store
        self._nav_provider = nav_provider
        self._fee_structure_for_fund = fee_structure_for_fund
        self._transfers = transfer_adapter

    async def run_once(self) -> list[AllocatorRedemption]:
        """Process every APPROVED redemption whose grace-period has expired."""

        processed: list[AllocatorRedemption] = []
        now = datetime.now(UTC)
        for redemption in self._store.list_pending_redemptions():
            if redemption.status is not RedemptionStatus.APPROVED:
                continue
            expiry = redemption.requested_timestamp + timedelta(days=redemption.grace_period_days)
            if now < expiry:
                continue
            try:
                processed.append(await self._drive(redemption))
            except Exception:  # shard-level isolation per redemption
                logger.exception(
                    "Grace-period processing failed for redemption_id=%s",
                    redemption.redemption_id,
                )
        return processed

    async def _drive(self, redemption: AllocatorRedemption) -> AllocatorRedemption:
        snapshot = self._nav_provider.latest_snapshot(redemption.fund_id, redemption.share_class)
        if snapshot is None:
            raise RuntimeError(
                f"No NAV snapshot available for "
                f"fund={redemption.fund_id} share_class={redemption.share_class}"
            )
        # Units-per-unit NAV: in the scaffold we read nav_usd as the per-unit NAV
        # directly; a proper units-outstanding divisor lands with the SQL store.
        settlement_nav = Decimal(snapshot.nav_usd)
        fee_structure = self._fee_structure_for_fund[redemption.fund_id]
        fund_context = FundTransferContext(
            fund_id=redemption.fund_id,
            share_class=redemption.share_class,
            allocation_id=None,
        )
        transfer = await self._transfers.execute_withdrawal(
            venue="TREASURY",
            token=redemption.share_class,
            amount=redemption.units_to_redeem * settlement_nav,
            to_address=redemption.destination,
            chain=self._config.redemption_settlement_chain,
            fund_context=fund_context,
        )
        moved = process_redemption(
            redemption,
            settlement_snapshot=snapshot,
            settlement_nav=settlement_nav,
            fee_structure=fee_structure,
            settlement_reference=transfer.tx_hash or transfer.transfer_id,
        )
        self._store.put_redemption(moved)
        emit_fund_admin_event(
            REDEMPTION_PROCESSED,
            details={
                "redemption_id": moved.redemption_id,
                "fund_id": moved.fund_id,
                "share_class": moved.share_class,
                "cash_amount_due_usd": str(moved.cash_amount_due_usd),
                "settlement_reference": moved.settlement_reference,
            },
        )
        settled = settle_redemption(moved)
        self._store.put_redemption(settled)
        emit_fund_admin_event(
            REDEMPTION_SETTLED,
            details={
                "redemption_id": settled.redemption_id,
                "fund_id": settled.fund_id,
                "share_class": settled.share_class,
            },
        )
        return settled
