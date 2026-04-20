"""AllocatorSubscription state-machine transitions.

Transitions (UAC ``SubscriptionStatus``):

    PENDING  -> APPROVED | REJECTED
    APPROVED -> SETTLED

Every transition is a pure function: takes the current model plus transition
inputs, returns a new frozen ``AllocatorSubscription`` (UAC models are
``frozen=True``). Callers persist the return value through a
``PersistenceStore`` and emit the associated lifecycle event via
``events.emit_fund_admin_event``. The state machine does NOT touch the event
bus directly — keeping the transitions deterministic and trivially testable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Protocol

from unified_api_contracts import (
    AllocatorSubscription,
    FundNAVSnapshot,
    SubscriptionStatus,
)


class SubscriptionTransitionError(RuntimeError):
    """Raised when a transition is attempted from an illegal source status."""


class AmlKycDecision(Protocol):
    """Outcome of running an allocator through the AML/KYC gate."""

    approved: bool
    reason: str


class AmlKycGate(Protocol):
    """Adapter protocol for the upstream AML/KYC provider.

    The scaffold ships with an auto-approve stub; the real adapter is injected
    during the compliance-onboarding follow-up phase.
    """

    def evaluate(self, allocator_id: str, fund_id: str, share_class: str) -> AmlKycDecision: ...


class NavProvider(Protocol):
    """Reads the most recent ``FundNAVSnapshot`` for a fund+share-class."""

    def latest_snapshot(self, fund_id: str, share_class: str) -> FundNAVSnapshot | None: ...


def create_subscription(
    subscription_id: str,
    fund_id: str,
    allocator_id: str,
    share_class: str,
    requested_amount_usd: Decimal,
) -> AllocatorSubscription:
    """Build a fresh PENDING subscription."""

    return AllocatorSubscription(
        subscription_id=subscription_id,
        fund_id=fund_id,
        allocator_id=allocator_id,
        share_class=share_class,
        requested_amount_usd=requested_amount_usd,
        requested_timestamp=datetime.now(UTC),
        status=SubscriptionStatus.PENDING,
    )


def approve_subscription(
    subscription: AllocatorSubscription,
    nav_snapshot: FundNAVSnapshot,
    nav_per_unit: Decimal,
) -> AllocatorSubscription:
    """PENDING -> APPROVED. Resolves NAV strike and computes ``units_issued``."""

    if subscription.status is not SubscriptionStatus.PENDING:
        raise SubscriptionTransitionError(f"Cannot APPROVE from status={subscription.status.value}")
    if nav_per_unit <= 0:
        raise SubscriptionTransitionError("nav_per_unit must be > 0 to APPROVE")

    units_issued = subscription.requested_amount_usd / nav_per_unit
    return subscription.model_copy(
        update={
            "status": SubscriptionStatus.APPROVED,
            "nav_strike_snapshot_id": nav_snapshot.snapshot_id,
            "units_issued": units_issued,
            "approval_timestamp": datetime.now(UTC),
        }
    )


def reject_subscription(subscription: AllocatorSubscription, reason: str) -> AllocatorSubscription:
    """PENDING -> REJECTED. Terminal."""

    if subscription.status is not SubscriptionStatus.PENDING:
        raise SubscriptionTransitionError(f"Cannot REJECT from status={subscription.status.value}")
    return subscription.model_copy(
        update={
            "status": SubscriptionStatus.REJECTED,
            "rejection_reason": reason,
        }
    )


def settle_subscription(
    subscription: AllocatorSubscription,
) -> AllocatorSubscription:
    """APPROVED -> SETTLED. Terminal. Funds have landed in treasury."""

    if subscription.status is not SubscriptionStatus.APPROVED:
        raise SubscriptionTransitionError(f"Cannot SETTLE from status={subscription.status.value}")
    return subscription.model_copy(
        update={
            "status": SubscriptionStatus.SETTLED,
        }
    )
