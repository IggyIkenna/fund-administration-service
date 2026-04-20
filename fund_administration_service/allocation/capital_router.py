"""CapitalRouter — drives fund -> strategy capital allocations.

Orchestrates one rebalance pass by emitting a sequence of transfers against an
injected ``TransferAdapter`` (execution-service). The router:

* Is idempotent on ``allocation_id`` — a second call with the same id is a
  no-op that returns the existing ``FundAllocation``.
* Tags every ``execute_internal_transfer`` call with a
  ``FundTransferContext(fund_id, share_class, allocation_id)`` so PBMS can
  reconcile the movement back to the UAC ``FundAllocation``.
* Applies shard-level failure isolation — a failed transfer for one target
  leaves other targets in-flight; the router records a FAILED allocation but
  does not raise across the loop boundary.
* Emits ``FUND_ALLOCATION_REBALANCED`` per transfer attempt. Callers receive
  the final persisted ``FundAllocation`` records.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from unified_api_contracts import (
    AllocationExecutionStatus,
    FundAllocation,
)
from unified_trading_library import FUND_ALLOCATION_REBALANCED

from fund_administration_service.allocation.transfer_protocol import (
    FundTransferContext,
    TransferAdapter,
    TransferResult,
    TransferStatus,
)
from fund_administration_service.events import emit_fund_admin_event
from fund_administration_service.persistence import PersistenceStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AllocationTarget:
    """Single-strategy allocation target handed to the router."""

    allocation_id: str
    strategy_id: str
    target_amount_usd: Decimal
    venue: str
    from_wallet: str
    to_wallet: str
    token: str


class CapitalRouter:
    """Routes capital from fund treasury to strategy trading wallets.

    Construction: inject a ``TransferAdapter`` (mock for tests, live for prod)
    and a ``PersistenceStore``. All mutable state lives in the store — the
    router itself is stateless apart from the injected dependencies.
    """

    def __init__(
        self,
        transfer_adapter: TransferAdapter,
        store: PersistenceStore,
    ) -> None:
        self._transfers = transfer_adapter
        self._store = store

    async def rebalance(
        self,
        *,
        fund_id: str,
        share_class: str,
        targets: list[AllocationTarget],
    ) -> list[FundAllocation]:
        """Execute one rebalance pass. Returns every persisted ``FundAllocation``."""

        results: list[FundAllocation] = []
        for target in targets:
            existing = self._store.get_allocation(target.allocation_id)
            if existing is not None and existing.execution_status in (
                AllocationExecutionStatus.COMPLETED,
                AllocationExecutionStatus.FAILED,
            ):
                # Idempotent no-op: terminal allocations are never re-driven.
                results.append(existing)
                continue

            pending = FundAllocation(
                allocation_id=target.allocation_id,
                fund_id=fund_id,
                share_class=share_class,
                strategy_id=target.strategy_id,
                target_amount_usd=target.target_amount_usd,
                allocation_timestamp=datetime.now(UTC),
                execution_status=AllocationExecutionStatus.IN_PROGRESS,
            )
            self._store.put_allocation(pending)
            allocation = await self._execute_transfer(pending, target, fund_id, share_class)
            results.append(allocation)

        return results

    async def _execute_transfer(
        self,
        allocation: FundAllocation,
        target: AllocationTarget,
        fund_id: str,
        share_class: str,
    ) -> FundAllocation:
        fund_context = FundTransferContext(
            fund_id=fund_id,
            share_class=share_class,
            allocation_id=target.allocation_id,
        )
        try:
            transfer = await self._transfers.execute_internal_transfer(
                venue=target.venue,
                from_wallet=target.from_wallet,
                to_wallet=target.to_wallet,
                token=target.token,
                amount=target.target_amount_usd,
                params={},
                fund_context=fund_context,
            )
        except Exception as exc:  # shard-level isolation — never raise across the loop
            return self._record_transfer_failure(allocation, target, fund_id, share_class, exc)

        return self._record_transfer_outcome(allocation, target, fund_id, share_class, transfer)

    def _record_transfer_failure(
        self,
        allocation: FundAllocation,
        target: AllocationTarget,
        fund_id: str,
        share_class: str,
        exc: BaseException,
    ) -> FundAllocation:
        """Persist the FAILED allocation + emit the error event."""

        logger.exception("Transfer execution failed for %s", target.allocation_id)
        failed = allocation.model_copy(
            update={
                "execution_status": AllocationExecutionStatus.FAILED,
                "executed_timestamp": datetime.now(UTC),
            }
        )
        self._store.put_allocation(failed)
        emit_fund_admin_event(
            FUND_ALLOCATION_REBALANCED,
            details={
                "allocation_id": target.allocation_id,
                "fund_id": fund_id,
                "share_class": share_class,
                "status": AllocationExecutionStatus.FAILED.value,
                "error": str(exc),
            },
            severity="ERROR",
        )
        return failed

    def _record_transfer_outcome(
        self,
        allocation: FundAllocation,
        target: AllocationTarget,
        fund_id: str,
        share_class: str,
        transfer: TransferResult,
    ) -> FundAllocation:
        """Persist the terminal allocation + emit the per-transfer event."""

        terminal_status = (
            AllocationExecutionStatus.COMPLETED
            if transfer.status is TransferStatus.CONFIRMED
            else AllocationExecutionStatus.FAILED
        )
        final = allocation.model_copy(
            update={
                "execution_status": terminal_status,
                "executed_amount_usd": transfer.amount_transferred,
                "executed_timestamp": datetime.now(UTC),
            }
        )
        self._store.put_allocation(final)
        emit_fund_admin_event(
            FUND_ALLOCATION_REBALANCED,
            details={
                "allocation_id": target.allocation_id,
                "fund_id": fund_id,
                "share_class": share_class,
                "strategy_id": target.strategy_id,
                "target_amount_usd": str(target.target_amount_usd),
                "executed_amount_usd": str(transfer.amount_transferred),
                "transfer_id": transfer.transfer_id,
                "status": terminal_status.value,
            },
        )
        return final
