"""Unit tests for the reference in-memory persistence store."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from unified_api_contracts.internal import (
    AllocationExecutionStatus,
    AllocatorRedemption,
    AllocatorSubscription,
    FundAllocation,
    RedemptionStatus,
    SubscriptionStatus,
)

from fund_administration_service.persistence import InMemoryStore


def _sub(subscription_id: str, fund_id: str = "fund-A") -> AllocatorSubscription:
    return AllocatorSubscription(
        subscription_id=subscription_id,
        fund_id=fund_id,
        allocator_id="client-42",
        share_class="USDC",
        requested_amount_usd=Decimal("10000"),
        requested_timestamp=datetime.now(UTC),
        status=SubscriptionStatus.PENDING,
    )


def _red(redemption_id: str, status: RedemptionStatus) -> AllocatorRedemption:
    return AllocatorRedemption(
        redemption_id=redemption_id,
        fund_id="fund-A",
        allocator_id="client-42",
        share_class="USDC",
        units_to_redeem=Decimal("10"),
        destination="0xAB",
        requested_timestamp=datetime.now(UTC),
        status=status,
        grace_period_days=5,
    )


def _alloc(allocation_id: str, fund_id: str = "fund-A") -> FundAllocation:
    return FundAllocation(
        allocation_id=allocation_id,
        fund_id=fund_id,
        share_class="USDC",
        strategy_id="strat-1",
        target_amount_usd=Decimal("1000"),
        allocation_timestamp=datetime.now(UTC),
        execution_status=AllocationExecutionStatus.PENDING,
    )


def test_subscription_roundtrip() -> None:
    store = InMemoryStore()
    sub = _sub("sub-1")
    store.put_subscription(sub)
    assert store.get_subscription("sub-1") == sub
    assert store.get_subscription("missing") is None


def test_list_subscriptions_for_fund_filters_by_fund() -> None:
    store = InMemoryStore()
    store.put_subscription(_sub("sub-1", fund_id="fund-A"))
    store.put_subscription(_sub("sub-2", fund_id="fund-B"))
    store.put_subscription(_sub("sub-3", fund_id="fund-A"))
    a_ids = {s.subscription_id for s in store.list_subscriptions_for_fund("fund-A")}
    assert a_ids == {"sub-1", "sub-3"}


def test_pending_redemptions_list_only_approved() -> None:
    store = InMemoryStore()
    store.put_redemption(_red("red-1", RedemptionStatus.PENDING))
    store.put_redemption(_red("red-2", RedemptionStatus.APPROVED))
    store.put_redemption(_red("red-3", RedemptionStatus.APPROVED))
    store.put_redemption(_red("red-4", RedemptionStatus.SETTLED))
    ids = {r.redemption_id for r in store.list_pending_redemptions()}
    assert ids == {"red-2", "red-3"}


def test_allocation_list_for_fund() -> None:
    store = InMemoryStore()
    store.put_allocation(_alloc("alloc-1", fund_id="fund-A"))
    store.put_allocation(_alloc("alloc-2", fund_id="fund-B"))
    assert len(store.list_allocations_for_fund("fund-A")) == 1
    assert len(store.list_allocations_for_fund("fund-B")) == 1
    assert store.list_allocations_for_fund("fund-C") == []
