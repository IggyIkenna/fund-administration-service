"""Tests for background workers — GracePeriodHandler + NAVStrikeScheduler."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from unified_api_contracts.internal import (
    AllocatorRedemption,
    FeeStructure,
    FundNAVSnapshot,
    NAVSnapshotFrequency,
    RedemptionStatus,
)

from fund_administration_service.allocation.transfer_protocol import (
    TransferResult,
    TransferStatus,
)
from fund_administration_service.background import (
    GracePeriodHandler,
    NAVStrikeScheduler,
)
from fund_administration_service.config import FundAdministrationServiceConfig
from fund_administration_service.persistence import InMemoryStore


class _AdapterOK:
    async def execute_internal_transfer(
        self, venue, from_wallet, to_wallet, token, amount, params, fund_context=None
    ):
        return TransferResult(
            transfer_id=f"i-{uuid.uuid4().hex[:6]}",
            status=TransferStatus.CONFIRMED,
            amount_transferred=amount,
            fund_context=fund_context,
        )

    async def execute_withdrawal(self, venue, token, amount, to_address, chain, fund_context=None):
        return TransferResult(
            transfer_id=f"w-{uuid.uuid4().hex[:6]}",
            status=TransferStatus.CONFIRMED,
            tx_hash="0xmock",
            amount_transferred=amount,
            fund_context=fund_context,
        )

    async def execute_onchain_transfer(
        self, from_wallet_id, to_address, token, amount, chain, fund_context=None
    ):
        return TransferResult(
            transfer_id=f"o-{uuid.uuid4().hex[:6]}",
            status=TransferStatus.CONFIRMED,
            amount_transferred=amount,
            fund_context=fund_context,
        )


class _StaticNav:
    def __init__(self, snapshot: FundNAVSnapshot | None) -> None:
        self._s = snapshot

    def latest_snapshot(self, fund_id: str, share_class: str) -> FundNAVSnapshot | None:
        return self._s


def _snap() -> FundNAVSnapshot:
    return FundNAVSnapshot(
        snapshot_id="bg-snap-1",
        fund_id="fund-BG",
        snapshot_timestamp=datetime.now(UTC),
        frequency=NAVSnapshotFrequency.DAILY,
        nav_usd=Decimal("100"),
    )


def _redemption(status: RedemptionStatus, days_ago: int) -> AllocatorRedemption:
    return AllocatorRedemption(
        redemption_id=f"r-{uuid.uuid4().hex[:6]}",
        fund_id="fund-BG",
        allocator_id="client-BG",
        share_class="USDC",
        units_to_redeem=Decimal("5"),
        destination="0xDEAD",
        requested_timestamp=datetime.now(UTC) - timedelta(days=days_ago),
        status=status,
        grace_period_days=3,
    )


@pytest.mark.asyncio
async def test_grace_period_handler_drives_expired_redemptions() -> None:
    store = InMemoryStore()
    # 5 days ago with 3-day grace = expired, should settle.
    store.put_redemption(_redemption(RedemptionStatus.APPROVED, days_ago=5))
    # 1 day ago with 3-day grace = pending, should be skipped.
    store.put_redemption(_redemption(RedemptionStatus.APPROVED, days_ago=1))
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=_StaticNav(_snap()),
        fee_structure_for_fund={
            "fund-BG": FeeStructure(
                trader_fee_pct=Decimal("0.02"),
                odum_fee_pct=Decimal("0.01"),
            )
        },
        transfer_adapter=_AdapterOK(),
    )
    result = await handler.run_once()
    assert len(result) == 1
    assert result[0].status is RedemptionStatus.SETTLED


@pytest.mark.asyncio
async def test_grace_period_handler_skips_non_approved() -> None:
    store = InMemoryStore()
    store.put_redemption(_redemption(RedemptionStatus.PENDING, days_ago=10))
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=_StaticNav(_snap()),
        fee_structure_for_fund={
            "fund-BG": FeeStructure(
                trader_fee_pct=Decimal("0.02"),
                odum_fee_pct=Decimal("0.01"),
            )
        },
        transfer_adapter=_AdapterOK(),
    )
    result = await handler.run_once()
    assert result == []


@pytest.mark.asyncio
async def test_grace_period_handler_isolates_nav_miss() -> None:
    store = InMemoryStore()
    store.put_redemption(_redemption(RedemptionStatus.APPROVED, days_ago=10))
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=_StaticNav(None),  # NAV unavailable triggers RuntimeError
        fee_structure_for_fund={
            "fund-BG": FeeStructure(
                trader_fee_pct=Decimal("0.02"),
                odum_fee_pct=Decimal("0.01"),
            )
        },
        transfer_adapter=_AdapterOK(),
    )
    # Must NOT raise — shard-level isolation catches per-redemption failures.
    result = await handler.run_once()
    assert result == []


def test_nav_strike_scheduler_returns_snapshot_when_available() -> None:
    snap = _snap()
    scheduler = NAVStrikeScheduler(
        service_config=FundAdministrationServiceConfig(),
        nav_provider=_StaticNav(snap),
    )
    assert scheduler.cadence_seconds == 86400
    assert scheduler.tick("fund-BG", "USDC") == snap


def test_nav_strike_scheduler_returns_none_when_unavailable() -> None:
    scheduler = NAVStrikeScheduler(
        service_config=FundAdministrationServiceConfig(),
        nav_provider=_StaticNav(None),
    )
    assert scheduler.tick("fund-BG", "USDC") is None
