"""End-to-end lifecycle loop through the FastAPI app.

Exercises: subscribe -> approve -> settle -> rebalance allocation ->
request redemption -> approve -> process -> settle. Asserts every lifecycle
event fires in the expected order by wiring a capturing sink into UTL's
``setup_events``.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from unified_api_contracts import FundTransferContext
from unified_api_contracts.internal import (
    FeeStructure,
    FundNAVSnapshot,
    NAVSnapshotFrequency,
)
from unified_trading_library import setup_events

from fund_administration_service.allocation.transfer_protocol import (
    TransferResult,
    TransferStatus,
)
from fund_administration_service.api.main import _Container, create_app
from fund_administration_service.config import FundAdministrationServiceConfig
from fund_administration_service.persistence import InMemoryStore


class _MockTransferAdapter:
    async def execute_internal_transfer(
        self,
        venue: str,
        from_wallet: str,
        to_wallet: str,
        token: str,
        amount: Decimal,
        params: dict[str, str],
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return TransferResult(
            transfer_id=f"it-{uuid.uuid4().hex[:8]}",
            status=TransferStatus.CONFIRMED,
            amount_transferred=amount,
            fund_context=fund_context,
        )

    async def execute_withdrawal(
        self,
        venue: str,
        token: str,
        amount: Decimal,
        to_address: str,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return TransferResult(
            transfer_id=f"wd-{uuid.uuid4().hex[:8]}",
            status=TransferStatus.CONFIRMED,
            amount_transferred=amount,
            fund_context=fund_context,
        )

    async def execute_onchain_transfer(
        self,
        from_wallet_id: str,
        to_address: str,
        token: str,
        amount: Decimal,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return TransferResult(
            transfer_id=f"oc-{uuid.uuid4().hex[:8]}",
            status=TransferStatus.CONFIRMED,
            amount_transferred=amount,
            fund_context=fund_context,
        )


# SCHEMA_PROVENANCE_EXEMPT — test helper adapters, not domain messages.


@dataclass
class _CapturingSink:
    """Captures event names + client_id/correlation_id so tests can assert on ordering."""

    events: list[str]

    def write_event(self, name: str, metadata: dict[str, object]) -> None:
        self.events.append(name)

    def publish_coordination_event(self, event: object) -> None:
        return None

    def subscribe_coordination_events(
        self, event_type: str, callback: Callable[[object], None]
    ) -> None:
        return None


class _FixedNavProvider:
    def __init__(self) -> None:
        self._snapshot = FundNAVSnapshot(
            snapshot_id="snap-it-1",
            fund_id="fund-IT",
            snapshot_timestamp=datetime.now(UTC),
            frequency=NAVSnapshotFrequency.DAILY,
            nav_usd=Decimal("1000000"),
        )

    def latest_snapshot(self, fund_id: str, share_class: str) -> FundNAVSnapshot | None:
        return self._snapshot


class _AutoApproveAmlGate:
    def evaluate(self, allocator_id: str, fund_id: str, share_class: str) -> object:
        return _AmlDecision(approved=True, reason="auto")


@dataclass
class _AmlDecision:
    approved: bool
    reason: str


def _make_client() -> tuple[TestClient, _CapturingSink]:
    sink = _CapturingSink(events=[])
    setup_events(service_name="fund-administration-service", mode="live", sink=sink)
    container = _Container(
        service_config=FundAdministrationServiceConfig(),
        store=InMemoryStore(),
        nav_provider=_FixedNavProvider(),
        aml_gate=_AutoApproveAmlGate(),
        transfer_adapter=_MockTransferAdapter(),
        fee_structure_for_fund={
            "fund-IT": FeeStructure(
                trader_fee_pct=Decimal("0.02"),
                odum_fee_pct=Decimal("0.01"),
            )
        },
        last_nav_strike={},
    )
    return TestClient(create_app(container)), sink


def test_full_lifecycle_loop_emits_all_events_in_order() -> None:
    client, sink = _make_client()

    # 1. Subscribe
    r = client.post(
        "/subscriptions",
        json={
            "subscription_id": "sub-it-1",
            "fund_id": "fund-IT",
            "allocator_id": "client-IT",
            "share_class": "USDC",
            "requested_amount_usd": "10000",
        },
    )
    assert r.status_code == 200

    # 2. Approve
    r = client.post("/subscriptions/sub-it-1/approve", json={"nav_per_unit": "100"})
    assert r.status_code == 200
    assert r.json()["status"] == "APPROVED"

    # 3. Settle
    r = client.post("/subscriptions/sub-it-1/settle")
    assert r.status_code == 200
    assert r.json()["status"] == "SETTLED"

    # 4. Rebalance
    r = client.post(
        "/funds/fund-IT/allocations/rebalance",
        json={
            "share_class": "USDC",
            "targets": [
                {
                    "allocation_id": "alloc-it-1",
                    "strategy_id": "strat-it",
                    "target_amount_usd": "5000",
                    "venue": "BINANCE",
                    "from_wallet": "funding",
                    "to_wallet": "trading",
                    "token": "USDT",
                }
            ],
        },
    )
    assert r.status_code == 200

    # 5. Request redemption
    r = client.post(
        "/redemptions",
        json={
            "redemption_id": "red-it-1",
            "fund_id": "fund-IT",
            "allocator_id": "client-IT",
            "share_class": "USDC",
            "units_to_redeem": "10",
            "destination": "0xDEAD",
            "grace_period_days": 0,
        },
    )
    assert r.status_code == 200

    # 6. Approve redemption
    r = client.post("/redemptions/red-it-1/approve")
    assert r.status_code == 200

    # 7. Process redemption
    r = client.post(
        "/redemptions/red-it-1/process",
        json={"settlement_nav": "100", "settlement_reference": "tx-it"},
    )
    assert r.status_code == 200

    # 8. Settle redemption
    r = client.post("/redemptions/red-it-1/settle")
    assert r.status_code == 200

    # Assert all 10 lifecycle events fired in order (CapitalRouter emits
    # FUND_ALLOCATION_REBALANCED per transfer, the API emits a summary).
    expected_prefix = [
        "SUBSCRIPTION_REQUESTED",
        "SUBSCRIPTION_APPROVED",
        "SUBSCRIPTION_SETTLED",
        "FUND_ALLOCATION_REBALANCED",  # per transfer
        "FUND_ALLOCATION_REBALANCED",  # summary
        "REDEMPTION_REQUESTED",
        "REDEMPTION_APPROVED",
        "REDEMPTION_PROCESSED",
        "REDEMPTION_SETTLED",
    ]
    assert sink.events == expected_prefix


def test_health_endpoint_returns_ok() -> None:
    client, _sink = _make_client()
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["service"] == "fund-administration-service"
