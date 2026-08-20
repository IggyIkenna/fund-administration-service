"""Reference in-memory persistence store.

The concrete SQL / Firestore implementation is tracked in a follow-up phase of
``fund_administration_service_and_pooled_subscription_redemption_2026_04_20``.
For the initial scaffold we expose a ``PersistenceStore`` Protocol that service
code talks to; the in-memory implementation satisfies every test and gives us
a swap-in seam when the durable backend lands.

Intentionally synchronous (dict-under-lock). Concurrency semantics are
documented per method, and the API endpoints call into the store without
awaiting — callers that need async persistence should wrap the concrete impl
behind an async facade rather than changing this protocol.
"""

from __future__ import annotations

import threading
from decimal import Decimal
from typing import Protocol

from unified_api_contracts import (
    AllocatorRedemption,
    AllocatorSubscription,
    FundAllocation,
    FundNAVSnapshot,
)


class PersistenceStore(Protocol):
    """Durable-backend protocol for fund-administration domain objects."""

    # --- Subscriptions ---------------------------------------------------
    def put_subscription(self, subscription: AllocatorSubscription) -> None: ...

    def get_subscription(self, subscription_id: str) -> AllocatorSubscription | None: ...

    def list_subscriptions_for_fund(self, fund_id: str) -> list[AllocatorSubscription]: ...

    # --- Redemptions ------------------------------------------------------
    def put_redemption(self, redemption: AllocatorRedemption) -> None: ...

    def get_redemption(self, redemption_id: str) -> AllocatorRedemption | None: ...

    def list_pending_redemptions(self) -> list[AllocatorRedemption]: ...

    # --- Allocations ------------------------------------------------------
    def put_allocation(self, allocation: FundAllocation) -> None: ...

    def get_allocation(self, allocation_id: str) -> FundAllocation | None: ...

    def list_allocations_for_fund(self, fund_id: str) -> list[FundAllocation]: ...

    # --- NAV snapshots ------------------------------------------------------
    def put_nav_snapshot(self, snapshot: FundNAVSnapshot) -> None: ...

    def latest_nav_snapshot(self, fund_id: str) -> FundNAVSnapshot | None: ...

    # --- Units-outstanding ledger -------------------------------------------
    def get_units_outstanding(self, fund_id: str, share_class: str) -> Decimal: ...

    def adjust_units_outstanding(
        self, fund_id: str, share_class: str, delta: Decimal
    ) -> Decimal: ...


class InMemoryStore:
    """Thread-safe in-memory implementation of ``PersistenceStore``.

    Suitable for unit tests, local dev, and the initial CI smoke. Replace with a
    SQL/Firestore backed class in the durable-persistence follow-up.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscriptions: dict[str, AllocatorSubscription] = {}
        self._redemptions: dict[str, AllocatorRedemption] = {}
        self._allocations: dict[str, FundAllocation] = {}
        self._nav_snapshots: dict[str, FundNAVSnapshot] = {}
        self._units_outstanding: dict[tuple[str, str], Decimal] = {}

    def put_subscription(self, subscription: AllocatorSubscription) -> None:
        with self._lock:
            self._subscriptions[subscription.subscription_id] = subscription

    def get_subscription(self, subscription_id: str) -> AllocatorSubscription | None:
        with self._lock:
            return self._subscriptions.get(subscription_id)

    def list_subscriptions_for_fund(self, fund_id: str) -> list[AllocatorSubscription]:
        with self._lock:
            return [sub for sub in self._subscriptions.values() if sub.fund_id == fund_id]

    def put_redemption(self, redemption: AllocatorRedemption) -> None:
        with self._lock:
            self._redemptions[redemption.redemption_id] = redemption

    def get_redemption(self, redemption_id: str) -> AllocatorRedemption | None:
        with self._lock:
            return self._redemptions.get(redemption_id)

    def list_pending_redemptions(self) -> list[AllocatorRedemption]:
        with self._lock:
            return [red for red in self._redemptions.values() if red.status.value == "APPROVED"]

    def put_allocation(self, allocation: FundAllocation) -> None:
        with self._lock:
            self._allocations[allocation.allocation_id] = allocation

    def get_allocation(self, allocation_id: str) -> FundAllocation | None:
        with self._lock:
            return self._allocations.get(allocation_id)

    def list_allocations_for_fund(self, fund_id: str) -> list[FundAllocation]:
        with self._lock:
            return [alloc for alloc in self._allocations.values() if alloc.fund_id == fund_id]

    def put_nav_snapshot(self, snapshot: FundNAVSnapshot) -> None:
        """Store *snapshot* as the latest for its ``fund_id`` (last-write-wins
        by ``snapshot_timestamp``, never regresses to an older push)."""

        with self._lock:
            existing = self._nav_snapshots.get(snapshot.fund_id)
            if existing is None or snapshot.snapshot_timestamp >= existing.snapshot_timestamp:
                self._nav_snapshots[snapshot.fund_id] = snapshot

    def latest_nav_snapshot(self, fund_id: str) -> FundNAVSnapshot | None:
        with self._lock:
            return self._nav_snapshots.get(fund_id)

    def get_units_outstanding(self, fund_id: str, share_class: str) -> Decimal:
        """Current units outstanding for ``(fund_id, share_class)`` — ``0`` if
        never adjusted (honest, not a fake ``1`` that would mask a bug)."""

        with self._lock:
            return self._units_outstanding.get((fund_id, share_class), Decimal("0"))

    def adjust_units_outstanding(self, fund_id: str, share_class: str, delta: Decimal) -> Decimal:
        """Apply *delta* (positive = subscription settled, negative = redemption
        processed) to the running total; returns the updated total."""

        with self._lock:
            key = (fund_id, share_class)
            updated = self._units_outstanding.get(key, Decimal("0")) + delta
            self._units_outstanding[key] = updated
            return updated
