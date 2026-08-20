"""Tests for background workers — GracePeriodHandler + NAVStrikeScheduler."""

from __future__ import annotations

import asyncio
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
from fund_administration_service.subscription import (
    AmlKycDecision,
    approve_subscription,
    create_subscription,
    settle_subscription,
)


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

    async def execute_withdrawal(
        self, venue, token, amount, to_address, chain, fund_context=None, idempotency_key=None
    ):
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
            self, venue, token, amount, to_address, chain, fund_context=None, idempotency_key=None
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
    # Seed units_outstanding=1 for each (fund, share_class) so
    # settlement_nav == nav_usd exactly, preserving this test's existing
    # dollar-amount assertions below — the units-outstanding divisor math
    # itself is proven by a dedicated test.
    store.adjust_units_outstanding("fund-A", "USDC", Decimal("1"))
    store.adjust_units_outstanding("fund-B", "USDC", Decimal("1"))
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
    store.adjust_units_outstanding("fund-BG", "USDC", Decimal("1"))
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
async def test_grace_period_handler_writes_treasury_ledger_row_on_settle() -> None:
    """A settled redemption produces a queryable ``ledger_type=treasury`` row.

    Writer for the acked-but-previously-unimplemented ``ledger_type=treasury/
    client_id={cid}/`` partition (Phase 6 split decision, 2026-05-23 ack).
    """

    store = InMemoryStore()
    store.put_redemption(_redemption(RedemptionStatus.APPROVED, days_ago=5))
    store.adjust_units_outstanding("fund-BG", "USDC", Decimal("1"))
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
    settled = result[0]
    assert settled.status is RedemptionStatus.SETTLED

    rows = store.list_treasury_ledger_rows(client_id="client-BG")
    assert len(rows) == 1
    row = rows[0]
    assert row.event_id == settled.redemption_id
    assert row.client_id == "client-BG"
    assert row.counterparty_client_id is None
    assert row.delta == -settled.cash_amount_due_usd
    assert row.asset_group  # non-empty — documented default, not a blank field

    # A second, unrelated client's query must not see this row.
    assert store.list_treasury_ledger_rows(client_id="some-other-client") == []


@pytest.mark.asyncio
async def test_treasury_ledger_source_wallet_isolated_across_funds_in_one_tick() -> None:
    """Two DIFFERENT funds settling in the SAME `run_once()` tick resolve to
    DIFFERENT treasury source wallets on their ledger rows -- never the flat
    process-wide `treasury_wallet_id` default, which would commingle them."""

    from fund_administration_service.ledger import resolve_treasury_source_wallet_id

    class TwoFundNav:
        def latest_snapshot(self, fund_id: str, share_class: str) -> FundNAVSnapshot:
            return FundNAVSnapshot(
                snapshot_id=f"snap-{fund_id}",
                fund_id=fund_id,
                snapshot_timestamp=datetime.now(UTC),
                frequency=NAVSnapshotFrequency.DAILY,
                nav_usd=Decimal("100"),
            )

    store = InMemoryStore()
    store.put_redemption(
        AllocatorRedemption(
            redemption_id="red-fund-a",
            fund_id="fund-A",
            allocator_id="client-a",
            share_class="USDC",
            units_to_redeem=Decimal("2"),
            destination="0xA",
            requested_timestamp=datetime.now(UTC) - timedelta(days=5),
            status=RedemptionStatus.APPROVED,
            grace_period_days=3,
        )
    )
    store.put_redemption(
        AllocatorRedemption(
            redemption_id="red-fund-b",
            fund_id="fund-B",
            allocator_id="client-b",
            share_class="USDC",
            units_to_redeem=Decimal("3"),
            destination="0xB",
            requested_timestamp=datetime.now(UTC) - timedelta(days=5),
            status=RedemptionStatus.APPROVED,
            grace_period_days=3,
        )
    )
    store.adjust_units_outstanding("fund-A", "USDC", Decimal("1"))
    store.adjust_units_outstanding("fund-B", "USDC", Decimal("1"))
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=TwoFundNav(),
        fee_structure_for_fund={
            "fund-A": FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0")),
            "fund-B": FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0")),
        },
        transfer_adapter=_AdapterOK(),
    )

    result = await handler.run_once()
    assert len(result) == 2

    wallet_a = store.list_treasury_ledger_rows(client_id="client-a")[0].account_id
    wallet_b = store.list_treasury_ledger_rows(client_id="client-b")[0].account_id
    assert wallet_a != wallet_b
    assert wallet_a == resolve_treasury_source_wallet_id("fund-A", "USDC")
    assert wallet_b == resolve_treasury_source_wallet_id("fund-B", "USDC")
    # Neither resolves to the flat process-wide default -- that's the whole point.
    default_wallet = FundAdministrationServiceConfig().treasury_wallet_id
    assert wallet_a != default_wallet
    assert wallet_b != default_wallet


@pytest.mark.asyncio
async def test_grace_period_handler_prefers_seconds_over_days_when_expired() -> None:
    store = InMemoryStore()
    # 5h ago with grace_period_seconds=14400 (4h) => expired by the seconds
    # math, even though grace_period_days=5 would say "not for 5 days".
    store.put_redemption(
        _redemption_with_seconds(RedemptionStatus.APPROVED, hours_ago=5, grace_period_seconds=14400)
    )
    store.adjust_units_outstanding("fund-BG", "USDC", Decimal("1"))
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


@pytest.mark.asyncio
async def test_nav_strike_scheduler_run_forever_fires_tick_at_configured_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``run_forever`` calls ``tick()`` for every registered (fund_id, share_class)
    pair on every simulated sleep tick — not zero times ever (today's
    pre-wiring state)."""

    scheduler = NAVStrikeScheduler(
        service_config=FundAdministrationServiceConfig(),
        nav_provider=_StaticNav(_snap()),
    )
    tick_calls: list[tuple[str, str]] = []
    orig_tick = scheduler.tick

    def _counting_tick(fund_id: str, share_class: str) -> FundNAVSnapshot | None:
        tick_calls.append((fund_id, share_class))
        return orig_tick(fund_id, share_class)

    scheduler.tick = _counting_tick  # type: ignore[method-assign]

    call_count = 0

    async def _fake_sleep(seconds: float) -> None:
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(asyncio, "sleep", _fake_sleep)

    with pytest.raises(asyncio.CancelledError):
        await scheduler.run_forever(interval_seconds=99, fund_share_classes=[("fund-BG", "USDC")])

    assert tick_calls == [("fund-BG", "USDC"), ("fund-BG", "USDC")]


@pytest.mark.asyncio
async def test_units_outstanding_divisor_changes_settlement_nav_from_raw_nav_usd() -> None:
    """Subscribe-then-redeem: units_outstanding increments on subscription
    SETTLED and decrements on redemption PROCESSED, and GracePeriodHandler's
    settlement_nav is ``nav_usd / units_outstanding`` — never equal to raw
    ``nav_usd`` once units_outstanding != 1 (proves the real divisor replaced
    the old nav_usd-as-per-unit-NAV placeholder)."""

    store = InMemoryStore()
    fund_id, share_class = "fund-UNITS", "USDC"

    # Subscribe + settle: 1000 requested / 2.50 nav_per_unit = 400 units issued.
    sub = create_subscription(
        subscription_id="sub-units-1",
        fund_id=fund_id,
        allocator_id="client-units",
        share_class=share_class,
        requested_amount_usd=Decimal("1000"),
    )
    snap_for_sub = FundNAVSnapshot(
        snapshot_id="snap-sub",
        fund_id=fund_id,
        snapshot_timestamp=datetime.now(UTC),
        frequency=NAVSnapshotFrequency.DAILY,
        nav_usd=Decimal("1"),
    )
    approved_sub = approve_subscription(sub, snap_for_sub, nav_per_unit=Decimal("2.50"))
    settled_sub = settle_subscription(approved_sub)
    assert settled_sub.units_issued == Decimal("400")
    store.adjust_units_outstanding(fund_id, share_class, settled_sub.units_issued)
    assert store.get_units_outstanding(fund_id, share_class) == Decimal("400")

    # Redeem 100 of the 400 outstanding units; fund NAV is 20,000.
    redemption = AllocatorRedemption(
        redemption_id="red-units-1",
        fund_id=fund_id,
        allocator_id="client-units",
        share_class=share_class,
        units_to_redeem=Decimal("100"),
        destination="0xDEAD",
        requested_timestamp=datetime.now(UTC) - timedelta(days=10),
        status=RedemptionStatus.APPROVED,
        grace_period_days=3,
    )
    store.put_redemption(redemption)
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=_StaticNav(
            FundNAVSnapshot(
                snapshot_id="snap-red",
                fund_id=fund_id,
                snapshot_timestamp=datetime.now(UTC),
                frequency=NAVSnapshotFrequency.DAILY,
                nav_usd=Decimal("20000"),
            )
        ),
        fee_structure_for_fund={
            fund_id: FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0")),
        },
        transfer_adapter=_AdapterOK(),
    )
    result = await handler.run_once()

    assert len(result) == 1
    settled_redemption = result[0]
    # settlement_nav = 20000 / 400 = 50 per unit -- NOT raw nav_usd (20000).
    expected_cash_due = Decimal("100") * Decimal("50")
    assert settled_redemption.cash_amount_due_usd == expected_cash_due
    assert settled_redemption.cash_amount_due_usd != Decimal("100") * Decimal("20000")
    # Units outstanding decremented by the 100 redeemed -> 300 remain.
    assert store.get_units_outstanding(fund_id, share_class) == Decimal("300")


@pytest.mark.asyncio
async def test_run_once_strikes_one_snapshot_per_fund_share_class_per_tick() -> None:
    store = InMemoryStore()
    # Two APPROVED, grace-expired redemptions for the same fund/share class.
    store.put_redemption(_redemption(RedemptionStatus.APPROVED, days_ago=5))
    store.put_redemption(_redemption(RedemptionStatus.APPROVED, days_ago=5))
    store.adjust_units_outstanding("fund-BG", "USDC", Decimal("100"))

    class CountingNav:
        def __init__(self) -> None:
            self.calls = 0

        def latest_snapshot(self, fund_id: str, share_class: str) -> FundNAVSnapshot:
            self.calls += 1
            return FundNAVSnapshot(
                snapshot_id=f"snap-{self.calls}",
                fund_id=fund_id,
                snapshot_timestamp=datetime.now(UTC),
                frequency=NAVSnapshotFrequency.DAILY,
                nav_usd=Decimal("100"),
            )

    nav = CountingNav()
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=nav,
        fee_structure_for_fund={
            "fund-BG": FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0"))
        },
        transfer_adapter=_AdapterOK(),
    )
    result = await handler.run_once()

    assert len(result) == 2
    # ONE snapshot strike for the shared (fund, share_class), reused by both
    # redemptions — identical snapshot_id, and the provider was called once.
    snapshot_ids = {red.redemption_nav_snapshot_id for red in result}
    assert snapshot_ids == {"snap-1"}
    assert nav.calls == 1


async def test_withdraw_to_allocator_carries_allocator_client_id() -> None:
    """Each redemption's withdrawal carries ITS OWN ``client_id`` (= allocator_id).

    execution-service enforces the client-funds-isolation HARD RULE against this
    ``client_id`` at the TransferAdapter boundary (raises
    ``CrossClientTransferForbiddenError`` on a mismatch with the process-bound
    ``CLIENT_ID``), so a batch that cross-wires one redemption's context into
    another's withdrawal is rejected there, not silently paid out.  This proves
    the fund-admin side sends the right identity per redemption.
    """

    class RecordingAdapter(_AdapterOK):
        def __init__(self) -> None:
            self.client_ids: list[str] = []

        async def execute_withdrawal(
            self, venue, token, amount, to_address, chain, fund_context=None, idempotency_key=None
        ):
            assert fund_context is not None
            assert fund_context.client_id is not None
            self.client_ids.append(fund_context.client_id)
            return await super().execute_withdrawal(
                venue, token, amount, to_address, chain, fund_context
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
            "destination": "0xCLIENT_B",
        }
    )
    store.put_redemption(first)
    store.put_redemption(second)
    # Seed units_outstanding so the real units-outstanding NAV divisor
    # (companion plan's _drive_unchecked) resolves per-unit NAV.
    store.adjust_units_outstanding("fund-A", "USDC", Decimal("1"))
    store.adjust_units_outstanding("fund-B", "USDC", Decimal("1"))
    adapter = RecordingAdapter()
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=_StaticNav(_snap()),
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
    # Each withdrawal's client_id == that redemption's own allocator (never a
    # shared/cross-wired context across the two allocators in the same tick).
    assert sorted(adapter.client_ids) == ["client-A", "client-B"]


@pytest.mark.asyncio
async def test_crashed_tick_does_not_double_withdraw_on_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ``run_once()`` tick that crashes after the withdrawal succeeds but
    before the redemption is persisted must NOT double-withdraw on the retry
    tick: the withdrawal's idempotency key IS the ``redemption_id``, so the
    adapter dedupes the repeated call (returns the cached result) and only ONE
    real withdrawal is issued for that redemption.
    """

    class IdempotentAdapter(_AdapterOK):
        def __init__(self) -> None:
            self._issued: dict[str, TransferResult] = {}
            self.withdrawal_keys: list[str] = []

        async def execute_withdrawal(
            self, venue, token, amount, to_address, chain, fund_context=None, idempotency_key=None
        ):
            assert idempotency_key is not None
            if idempotency_key in self._issued:
                return self._issued[idempotency_key]
            result = await super().execute_withdrawal(
                venue, token, amount, to_address, chain, fund_context
            )
            self.withdrawal_keys.append(idempotency_key)
            self._issued[idempotency_key] = result
            return result

    store = InMemoryStore()
    redemption = _redemption(RedemptionStatus.APPROVED, days_ago=5)
    store.put_redemption(redemption)
    store.adjust_units_outstanding("fund-BG", "USDC", Decimal("1"))
    adapter = IdempotentAdapter()
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=_StaticNav(_snap()),
        fee_structure_for_fund={
            "fund-BG": FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0"))
        },
        transfer_adapter=adapter,
    )

    # Tick 1: withdrawal succeeds, then persistence crashes (simulated by
    # patching _persist_processed to raise) -> the redemption stays APPROVED
    # and past its expiry.
    real_persist = handler._persist_processed

    def _crash(*args: object, **kwargs: object) -> object:
        raise RuntimeError("simulated persist crash")

    monkeypatch.setattr(handler, "_persist_processed", _crash)
    result1 = await handler.run_once()
    assert result1 == []  # crash isolated per redemption
    assert adapter.withdrawal_keys == [redemption.redemption_id]

    # Tick 2: the same redemption is retried. The adapter dedupes on the
    # idempotency key (= redemption_id) -> NO second real withdrawal issued.
    monkeypatch.setattr(handler, "_persist_processed", real_persist)
    result2 = await handler.run_once()
    assert len(result2) == 1
    assert result2[0].status is RedemptionStatus.SETTLED
    assert adapter.withdrawal_keys == [redemption.redemption_id]


@pytest.mark.asyncio
async def test_rejected_aml_at_expiry_does_not_pay_out() -> None:
    """AML/KYC clearance is re-checked at grace-period-EXPIRY time, not just at
    subscription-request time: a redemption whose allocator's AML status has
    flipped to rejected between request and expiry is NOT paid out (no
    withdrawal is issued)."""

    class RejectingDecision:
        approved: bool = False
        reason: str = "aml-rejected"

    class RejectingAmlGate:
        def evaluate(self, allocator_id: str, fund_id: str, share_class: str) -> AmlKycDecision:
            return RejectingDecision()

    class RecordingAdapter(_AdapterOK):
        def __init__(self) -> None:
            self.withdrawal_count = 0

        async def execute_withdrawal(
            self, venue, token, amount, to_address, chain, fund_context=None, idempotency_key=None
        ):
            self.withdrawal_count += 1
            return await super().execute_withdrawal(
                venue, token, amount, to_address, chain, fund_context, idempotency_key
            )

    store = InMemoryStore()
    redemption = _redemption(RedemptionStatus.APPROVED, days_ago=5)
    store.put_redemption(redemption)
    store.adjust_units_outstanding("fund-BG", "USDC", Decimal("1"))
    adapter = RecordingAdapter()
    handler = GracePeriodHandler(
        service_config=FundAdministrationServiceConfig(),
        store=store,
        nav_provider=_StaticNav(_snap()),
        fee_structure_for_fund={
            "fund-BG": FeeStructure(trader_fee_pct=Decimal("0"), odum_fee_pct=Decimal("0"))
        },
        transfer_adapter=adapter,
        aml_gate=RejectingAmlGate(),
    )

    result = await handler.run_once()

    # The gate rejected at grace-expiry -> the redemption is NOT paid out.
    assert result == []
    assert adapter.withdrawal_count == 0
