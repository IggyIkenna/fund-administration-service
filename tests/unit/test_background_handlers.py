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


def _redemption_with_seconds(
    status: RedemptionStatus, hours_ago: int, grace_period_seconds: int
) -> AllocatorRedemption:
    return AllocatorRedemption(
        redemption_id=f"r-{uuid.uuid4().hex[:6]}",
        fund_id="fund-BG",
        allocator_id="client-BG",
        share_class="USDC",
        units_to_redeem=Decimal("5"),
        destination="0xDEAD",
        requested_timestamp=datetime.now(UTC) - timedelta(hours=hours_ago),
        status=status,
        # Deliberately a multi-day fallback so a test proving expiry actually
        # used grace_period_seconds (not grace_period_days) cannot pass by
        # accident via the days-based math.
        grace_period_days=5,
        grace_period_seconds=grace_period_seconds,
    )


@pytest.mark.asyncio
async def test_grace_period_handler_keeps_multi_client_withdrawals_isolated() -> None:
    class RecordingAdapter(_AdapterOK):
        def __init__(self) -> None:
            self.withdrawals: list[tuple[str, Decimal, str]] = []

        async def execute_withdrawal(
            self, venue, token, amount, to_address, chain, fund_context=None
        ):
            assert fund_context is not None
            self.withdrawals.append((to_address, amount, fund_context.fund_id))
            return await super().execute_withdrawal(
                venue, token, amount, to_address, chain, fund_context
            )

    class FundNav:
        def latest_snapshot(self, fund_id: str, share_class: str) -> FundNAVSnapshot:
            return FundNAVSnapshot(
                snapshot_id=f"snap-{fund_id}",
                fund_id=fund_id,
                snapshot_timestamp=datetime.now(UTC),
                frequency=NAVSnapshotFrequency.DAILY,
                nav_usd=Decimal("100"),
            )

    store = InMemoryStore()
    first = AllocatorRedemption(
        redemption_id="redemption-client-a",
        fund_id="fund-A",
        allocator_id="client-A",
        share_class="USDC",
        units_to_redeem=Decimal("2"),
        destination="0xCLIENT_A",
        requested_timestamp=datetime.now(UTC) - timedelta(days=5),
        status=RedemptionStatus.APPROVED,
        grace_period_days=3,
    )
    second = first.model_copy(
        update={
            "redemption_id": "redemption-client-b",
            "fund_id": "fund-B",
            "allocator_id": "client-B",
            "units_to_redeem": Decimal("3"),
            "destination": "0xCLIENT_B",
        }
    )
    store.put_redemption(first)
    store.put_redemption(second)
    adapter = RecordingAdapter()
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=FundNav(),
        fee_structure_for_fund={
            "fund-A": FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0")),
            "fund-B": FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0")),
        },
        transfer_adapter=adapter,
    )

    result = await handler.run_once()

    assert {redemption.redemption_id for redemption in result} == {
        "redemption-client-a",
        "redemption-client-b",
    }
    assert len(adapter.withdrawals) == 2
    withdrawals = {
        destination: (amount, fund_id) for destination, amount, fund_id in adapter.withdrawals
    }
    assert withdrawals == {
        "0xCLIENT_A": (Decimal("200"), "fund-A"),
        "0xCLIENT_B": (Decimal("300"), "fund-B"),
    }


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
async def test_grace_period_handler_prefers_seconds_over_days_when_expired() -> None:
    store = InMemoryStore()
    # 5h ago with grace_period_seconds=14400 (4h) => expired by the seconds
    # math, even though grace_period_days=5 would say "not for 5 days".
    store.put_redemption(
        _redemption_with_seconds(RedemptionStatus.APPROVED, hours_ago=5, grace_period_seconds=14400)
    )
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
async def test_grace_period_handler_seconds_not_yet_expired_is_skipped() -> None:
    store = InMemoryStore()
    # 2h ago with grace_period_seconds=14400 (4h) => not yet expired by the
    # seconds math.
    store.put_redemption(
        _redemption_with_seconds(RedemptionStatus.APPROVED, hours_ago=2, grace_period_seconds=14400)
    )
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


class _StopLoopError(Exception):
    """Sentinel raised by the monkeypatched sleep to end the loop deterministically."""


@pytest.mark.asyncio
async def test_grace_period_handler_run_forever_fires_at_configured_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=InMemoryStore(),
        nav_provider=_StaticNav(None),
        fee_structure_for_fund={},
        transfer_adapter=_AdapterOK(),
    )

    run_once_calls = 0
    real_run_once = handler.run_once

    async def _counting_run_once() -> list[AllocatorRedemption]:
        nonlocal run_once_calls
        run_once_calls += 1
        return await real_run_once()

    monkeypatch.setattr(handler, "run_once", _counting_run_once)

    sleep_intervals: list[int] = []

    async def _fast_sleep(seconds: int) -> None:
        sleep_intervals.append(seconds)
        if len(sleep_intervals) >= 3:
            # Deterministically end the loop after 3 iterations — no real
            # event-loop scheduling/cancellation timing games needed.
            raise _StopLoopError

    monkeypatch.setattr(
        "fund_administration_service.background.grace_period_handler.asyncio.sleep",
        _fast_sleep,
    )

    # Today's state (before this method existed) is zero calls ever — assert
    # the loop actually fires repeatedly, at the configured interval.
    with pytest.raises(_StopLoopError):
        await handler.run_forever(interval_seconds=999)

    assert run_once_calls == 3
    assert sleep_intervals == [999, 999, 999]


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
