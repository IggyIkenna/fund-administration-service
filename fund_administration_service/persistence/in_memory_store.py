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
from typing import Protocol

from unified_api_contracts.internal import (
    AllocatorRedemption,
    AllocatorSubscription,
    FundAllocation,
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
