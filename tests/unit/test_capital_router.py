"""Unit tests for ``CapitalRouter``."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from unified_api_contracts.internal import AllocationExecutionStatus

from fund_administration_service.allocation import AllocationTarget, CapitalRouter
from fund_administration_service.allocation.transfer_protocol import (
    FundTransferContext,
    TransferResult,
    TransferStatus,
)
from fund_administration_service.persistence import InMemoryStore


class MockTransferAdapter:
    """In-test transfer adapter — deterministic instant CONFIRMED results."""

    async def execute_internal_transfer(
        self,
        venue: str,
        from_wallet: str,
        to_wallet: str,
        token: str,
        amount: Decimal,
        params: dict[str, str],
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return TransferResult(
            transfer_id=f"mock-{uuid.uuid4().hex[:8]}",
            status=TransferStatus.CONFIRMED,
            amount_transferred=amount,
            fund_context=fund_context,
        )

    async def execute_withdrawal(
        self,
        venue: str,
        token: str,
        amount: Decimal,
        to_address: str,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return TransferResult(
            transfer_id=f"mock-wd-{uuid.uuid4().hex[:8]}",
            status=TransferStatus.CONFIRMED,
            tx_hash="0xmocktx",
            amount_transferred=amount,
            fund_context=fund_context,
        )

    async def execute_onchain_transfer(
        self,
        from_wallet_id: str,
        to_address: str,
        token: str,
        amount: Decimal,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return TransferResult(
            transfer_id=f"mock-oc-{uuid.uuid4().hex[:8]}",
            status=TransferStatus.CONFIRMED,
            tx_hash="0xmocktx",
            amount_transferred=amount,
            fund_context=fund_context,
        )


def _targets(allocation_id: str = "alloc-1") -> list[AllocationTarget]:
    return [
        AllocationTarget(
            allocation_id=allocation_id,
            strategy_id="strat-eth-carry",
            target_amount_usd=Decimal("10000"),
            venue="BINANCE",
            from_wallet="funding",
            to_wallet="trading",
            token="USDT",
        )
    ]


@pytest.mark.asyncio
async def test_rebalance_completes_with_mock_adapter() -> None:
    store = InMemoryStore()
    router = CapitalRouter(MockTransferAdapter(), store)
    result = await router.rebalance(fund_id="fund-A", share_class="USDC", targets=_targets())
    assert len(result) == 1
    assert result[0].execution_status is AllocationExecutionStatus.COMPLETED
    persisted = store.get_allocation("alloc-1")
    assert persisted is not None
    assert persisted.execution_status is AllocationExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_rebalance_is_idempotent_on_allocation_id() -> None:
    store = InMemoryStore()
    router = CapitalRouter(MockTransferAdapter(), store)
    first = await router.rebalance(
        fund_id="fund-A", share_class="USDC", targets=_targets("alloc-1")
    )
    assert first[0].execution_status is AllocationExecutionStatus.COMPLETED
    # Second call with the same id must be a no-op (adapter not called again)
    second = await router.rebalance(
        fund_id="fund-A", share_class="USDC", targets=_targets("alloc-1")
    )
    assert second[0].allocation_id == first[0].allocation_id
    # Terminal COMPLETED: router returns the persisted record unchanged.
    assert second[0].execution_status is AllocationExecutionStatus.COMPLETED


@pytest.mark.asyncio
async def test_rebalance_isolates_failures() -> None:
    class _BoomAdapter:
        async def execute_internal_transfer(
            self,
            venue: str,
            from_wallet: str,
            to_wallet: str,
            token: str,
            amount: Decimal,
            params: dict[str, str],
            fund_context: FundTransferContext | None = None,
        ) -> TransferResult:
            raise RuntimeError("venue down")

        async def execute_withdrawal(
            self,
            venue: str,
            token: str,
            amount: Decimal,
            to_address: str,
            chain: str,
            fund_context: FundTransferContext | None = None,
        ) -> TransferResult:
            raise RuntimeError("venue down")

        async def execute_onchain_transfer(
            self,
            from_wallet_id: str,
            to_address: str,
            token: str,
            amount: Decimal,
            chain: str,
            fund_context: FundTransferContext | None = None,
        ) -> TransferResult:
            raise RuntimeError("venue down")

    store = InMemoryStore()
    router = CapitalRouter(_BoomAdapter(), store)
    result = await router.rebalance(
        fund_id="fund-A",
        share_class="USDC",
        targets=[
            AllocationTarget(
                allocation_id="alloc-boom",
                strategy_id="strat-x",
                target_amount_usd=Decimal("5000"),
                venue="BINANCE",
                from_wallet="funding",
                to_wallet="trading",
                token="USDT",
            ),
            AllocationTarget(
                allocation_id="alloc-ok",
                strategy_id="strat-y",
                target_amount_usd=Decimal("1000"),
                venue="BINANCE",
                from_wallet="funding",
                to_wallet="trading",
                token="USDT",
            ),
        ],
    )
    # Both targets should have records (shard-level isolation — first failure
    # does not prevent the second from being attempted).
    assert len(result) == 2
    statuses = {a.allocation_id: a.execution_status for a in result}
    assert statuses["alloc-boom"] is AllocationExecutionStatus.FAILED
    assert statuses["alloc-ok"] is AllocationExecutionStatus.FAILED
