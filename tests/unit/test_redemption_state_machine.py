"""Unit tests for the redemption state machine (pure-function transitions)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from unified_api_contracts.internal import (
    FeeStructure,
    FundNAVSnapshot,
    NAVSnapshotFrequency,
    RedemptionStatus,
)

from fund_administration_service.redemption.state_machine import (
    RedemptionTransitionError,
    approve_redemption,
    create_redemption,
    process_redemption,
    reject_redemption,
    settle_redemption,
)


def _snapshot() -> FundNAVSnapshot:
    return FundNAVSnapshot(
        snapshot_id="snap-red-1",
        fund_id="fund-A",
        snapshot_timestamp=datetime.now(UTC),
        frequency=NAVSnapshotFrequency.DAILY,
        nav_usd=Decimal("1000000"),
    )


def _fees() -> FeeStructure:
    return FeeStructure(
        trader_fee_pct=Decimal("0.02"),
        odum_fee_pct=Decimal("0.01"),
    )


def test_happy_path_pending_to_settled() -> None:
    red = create_redemption(
        redemption_id="red-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        units_to_redeem=Decimal("50"),
        destination="0xDEAD",
        grace_period_days=5,
    )
    assert red.status is RedemptionStatus.PENDING
    approved = approve_redemption(red)
    assert approved.status is RedemptionStatus.APPROVED
    processed = process_redemption(
        approved,
        settlement_snapshot=_snapshot(),
        settlement_nav=Decimal("100"),
        fee_structure=_fees(),
        settlement_reference="tx-abc",
    )
    # 50 * 100 = 5000 ; fees = 5000 * 0.03 = 150 ; cash_due = 4850
    assert processed.status is RedemptionStatus.PROCESSED
    assert processed.cash_amount_due_usd == Decimal("4850.00")
    assert processed.settlement_reference == "tx-abc"
    settled = settle_redemption(processed)
    assert settled.status is RedemptionStatus.SETTLED
    assert settled.settlement_timestamp is not None


def test_reject_from_pending() -> None:
    red = create_redemption(
        redemption_id="red-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        units_to_redeem=Decimal("50"),
        destination="0xDEAD",
        grace_period_days=5,
    )
    rejected = reject_redemption(red, reason="insufficient liquidity")
    assert rejected.status is RedemptionStatus.REJECTED
    assert rejected.rejection_reason == "insufficient liquidity"


def test_illegal_transitions_raise() -> None:
    red = create_redemption(
        redemption_id="red-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        units_to_redeem=Decimal("50"),
        destination="0xDEAD",
        grace_period_days=5,
    )
    with pytest.raises(RedemptionTransitionError):
        process_redemption(
            red, _snapshot(), Decimal("100"), _fees(), "tx"
        )  # PENDING -> PROCESSED is not allowed
    with pytest.raises(RedemptionTransitionError):
        settle_redemption(red)  # PENDING -> SETTLED is not allowed


def test_process_requires_positive_nav() -> None:
    red = approve_redemption(
        create_redemption(
            redemption_id="red-1",
            fund_id="fund-A",
            allocator_id="client-42",
            share_class="USDC",
            units_to_redeem=Decimal("50"),
            destination="0xDEAD",
            grace_period_days=5,
        )
    )
    with pytest.raises(RedemptionTransitionError):
        process_redemption(red, _snapshot(), Decimal("0"), _fees(), "tx")


def test_redemption_fee_pct_deducted_only_from_redeemed_amount() -> None:
    red = approve_redemption(
        create_redemption(
            redemption_id="red-1",
            fund_id="fund-A",
            allocator_id="client-42",
            share_class="USDC",
            units_to_redeem=Decimal("50"),
            destination="0xDEAD",
            grace_period_days=5,
        )
    )
    # No redemption fee: 50 * 100 = 5000 gross; 3% perf fees = 150; cash_due = 4850.
    without_fee = process_redemption(red, _snapshot(), Decimal("100"), _fees(), "tx-1")
    assert without_fee.cash_amount_due_usd == Decimal("4850.00")

    # 1% redemption fee charged only on this redemption's own gross amount.
    with_fee = process_redemption(
        red,
        _snapshot(),
        Decimal("100"),
        FeeStructure(
            trader_fee_pct=Decimal("0.02"),
            odum_fee_pct=Decimal("0.01"),
            redemption_fee_pct=Decimal("0.01"),
        ),
        "tx-2",
    )
    # gross 5000; total fee 4% = 200; cash_due = 4800.
    assert with_fee.cash_amount_due_usd == Decimal("4800.00")
    # The 1% redemption fee removed exactly 5000 * 0.01 = 50 from THIS redemption
    # only — the fee-less redemption's math is untouched (4850), proving the fee
    # is per-redemption, never socialized across the fund.
    assert without_fee.cash_amount_due_usd - with_fee.cash_amount_due_usd == Decimal("50.00")
