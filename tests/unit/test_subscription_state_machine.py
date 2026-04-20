"""Unit tests for the subscription state machine (pure-function transitions)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from unified_api_contracts.internal import (
    FundNAVSnapshot,
    NAVSnapshotFrequency,
    SubscriptionStatus,
)

from fund_administration_service.subscription.state_machine import (
    SubscriptionTransitionError,
    approve_subscription,
    create_subscription,
    reject_subscription,
    settle_subscription,
)


def _snapshot(nav_usd: Decimal) -> FundNAVSnapshot:
    return FundNAVSnapshot(
        snapshot_id="snap-1",
        fund_id="fund-A",
        snapshot_timestamp=datetime.now(UTC),
        frequency=NAVSnapshotFrequency.DAILY,
        nav_usd=nav_usd,
    )


def test_create_subscription_is_pending() -> None:
    sub = create_subscription(
        subscription_id="sub-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        requested_amount_usd=Decimal("10000"),
    )
    assert sub.status is SubscriptionStatus.PENDING
    assert sub.units_issued is None


def test_approve_moves_to_approved_and_computes_units() -> None:
    sub = create_subscription(
        subscription_id="sub-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        requested_amount_usd=Decimal("10000"),
    )
    snapshot = _snapshot(nav_usd=Decimal("1000000"))
    approved = approve_subscription(sub, snapshot, nav_per_unit=Decimal("100"))
    assert approved.status is SubscriptionStatus.APPROVED
    assert approved.units_issued == Decimal("100")
    assert approved.nav_strike_snapshot_id == "snap-1"


def test_approve_from_non_pending_raises() -> None:
    sub = create_subscription(
        subscription_id="sub-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        requested_amount_usd=Decimal("10000"),
    )
    snapshot = _snapshot(nav_usd=Decimal("1000000"))
    approved = approve_subscription(sub, snapshot, Decimal("100"))
    with pytest.raises(SubscriptionTransitionError):
        approve_subscription(approved, snapshot, Decimal("100"))


def test_approve_with_non_positive_nav_raises() -> None:
    sub = create_subscription(
        subscription_id="sub-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        requested_amount_usd=Decimal("10000"),
    )
    snapshot = _snapshot(nav_usd=Decimal("0"))
    with pytest.raises(SubscriptionTransitionError):
        approve_subscription(sub, snapshot, nav_per_unit=Decimal("0"))


def test_reject_from_pending() -> None:
    sub = create_subscription(
        subscription_id="sub-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        requested_amount_usd=Decimal("10000"),
    )
    rejected = reject_subscription(sub, reason="AML failed")
    assert rejected.status is SubscriptionStatus.REJECTED
    assert rejected.rejection_reason == "AML failed"


def test_settle_only_from_approved() -> None:
    sub = create_subscription(
        subscription_id="sub-1",
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        requested_amount_usd=Decimal("10000"),
    )
    with pytest.raises(SubscriptionTransitionError):
        settle_subscription(sub)
    approved = approve_subscription(sub, _snapshot(Decimal("1000000")), Decimal("100"))
    settled = settle_subscription(approved)
    assert settled.status is SubscriptionStatus.SETTLED
