"""AllocatorRedemption state-machine transitions.

Transitions (UAC ``RedemptionStatus``):

    PENDING   -> APPROVED | REJECTED
    APPROVED  -> PROCESSED
    PROCESSED -> SETTLED

Redemption fees are computed at settlement NAV using
``unified_api_contracts.internal.reporting.FeeStructure``. The state machine is
pure: it computes the new frozen model and returns it; the caller handles
persistence and event emission.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from unified_api_contracts.internal import (
    AllocatorRedemption,
    FeeStructure,
    FundNAVSnapshot,
    RedemptionStatus,
)


class RedemptionTransitionError(RuntimeError):
    """Raised when a transition is attempted from an illegal source status."""


def create_redemption(
    redemption_id: str,
    fund_id: str,
    allocator_id: str,
    share_class: str,
    units_to_redeem: Decimal,
    destination: str,
    grace_period_days: int,
) -> AllocatorRedemption:
    """Build a fresh PENDING redemption."""

    return AllocatorRedemption(
        redemption_id=redemption_id,
        fund_id=fund_id,
        allocator_id=allocator_id,
        share_class=share_class,
        units_to_redeem=units_to_redeem,
        destination=destination,
        requested_timestamp=datetime.now(UTC),
        status=RedemptionStatus.PENDING,
        grace_period_days=grace_period_days,
    )


def approve_redemption(redemption: AllocatorRedemption) -> AllocatorRedemption:
    """PENDING -> APPROVED. Grace period countdown begins."""

    if redemption.status is not RedemptionStatus.PENDING:
        raise RedemptionTransitionError(f"Cannot APPROVE from status={redemption.status.value}")
    return redemption.model_copy(update={"status": RedemptionStatus.APPROVED})


def reject_redemption(redemption: AllocatorRedemption, reason: str) -> AllocatorRedemption:
    """PENDING -> REJECTED. Terminal."""

    if redemption.status is not RedemptionStatus.PENDING:
        raise RedemptionTransitionError(f"Cannot REJECT from status={redemption.status.value}")
    return redemption.model_copy(
        update={
            "status": RedemptionStatus.REJECTED,
            "rejection_reason": reason,
        }
    )


def process_redemption(
    redemption: AllocatorRedemption,
    settlement_snapshot: FundNAVSnapshot,
    settlement_nav: Decimal,
    fee_structure: FeeStructure,
    settlement_reference: str,
) -> AllocatorRedemption:
    """APPROVED -> PROCESSED. Computes ``cash_amount_due_usd``.

    ``cash_amount_due = units * settlement_nav - (units * settlement_nav * total_fee_pct)``
    where ``total_fee_pct`` is the sum of trader + odum performance fees from the
    injected ``FeeStructure``. Grace-period timing is the caller's gate — this
    function asserts the source status is APPROVED but does not evaluate elapsed
    business days; the ``GracePeriodHandler`` watchdog does that and invokes us
    only after the gate opens.
    """

    if redemption.status is not RedemptionStatus.APPROVED:
        raise RedemptionTransitionError(f"Cannot PROCESS from status={redemption.status.value}")
    if settlement_nav <= 0:
        raise RedemptionTransitionError("settlement_nav must be > 0 to PROCESS")

    gross_usd = redemption.units_to_redeem * settlement_nav
    total_fee_pct = fee_structure.trader_fee_pct + fee_structure.odum_fee_pct
    fees = gross_usd * total_fee_pct
    cash_due = gross_usd - fees

    return redemption.model_copy(
        update={
            "status": RedemptionStatus.PROCESSED,
            "redemption_nav_snapshot_id": settlement_snapshot.snapshot_id,
            "cash_amount_due_usd": cash_due,
            "settlement_reference": settlement_reference,
        }
    )


def settle_redemption(redemption: AllocatorRedemption) -> AllocatorRedemption:
    """PROCESSED -> SETTLED. Counterparty confirmed funds received."""

    if redemption.status is not RedemptionStatus.PROCESSED:
        raise RedemptionTransitionError(f"Cannot SETTLE from status={redemption.status.value}")
    return redemption.model_copy(
        update={
            "status": RedemptionStatus.SETTLED,
            "settlement_timestamp": datetime.now(UTC),
        }
    )
