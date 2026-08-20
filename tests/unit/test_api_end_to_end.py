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
    LocalSimulatedTransferAdapter,
    TransferResult,
    TransferStatus,
)
from fund_administration_service.api.main import (
    _build_default_container,
    _Container,
    create_app,
)
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


def test_build_default_container_wires_real_transfer_adapter_and_nav_provider() -> None:
    """`_default_container()`'s production path must never leave
    `transfer_adapter=None` or an always-empty `nav_provider` — both were
    permanent no-ops before this wiring landed. Mock/test containers built
    explicitly (as `_make_client()` above does) may still pass `None`."""

    container = _build_default_container()
    assert container.transfer_adapter is not None
    # The store-backed nav_provider is real (not the old always-None stub) —
    # prove it round-trips a snapshot pushed through the same store instance.
    assert container.nav_provider.latest_snapshot("fund-X", "USDC") is None
    snap = FundNAVSnapshot(
        snapshot_id="snap-default-container",
        fund_id="fund-X",
        snapshot_timestamp=datetime.now(UTC),
        frequency=NAVSnapshotFrequency.DAILY,
        nav_usd=Decimal("500"),
    )
    container.store.put_nav_snapshot(snap)
    assert container.nav_provider.latest_snapshot("fund-X", "USDC") == snap


def test_nav_snapshot_webhook_ingests_and_feeds_nav_provider() -> None:
    """`POST /nav-snapshots` (the position-balance-monitor-service webhook
    receiver) stores the pushed snapshot where the wired `NavProvider` reads
    it — proves the ingest route and the store-backed provider are the same
    real pipeline, not two disconnected stubs."""

    # Real container (not `_make_client()`'s fixed-snapshot test double) —
    # only `_build_default_container()`'s `_StoreBackedNavProvider` actually
    # reads what the webhook writes.
    client = TestClient(create_app(_build_default_container()))
    payload = {
        "snapshot_id": "snap-webhook-1",
        "fund_id": "fund-IT",
        "snapshot_timestamp": datetime.now(UTC).isoformat(),
        "frequency": "DAILY",
        "nav_usd": "250000",
    }
    r = client.post("/nav-snapshots", json=payload)
    assert r.status_code == 200
    r2 = client.get("/funds/fund-IT/nav/history", params={"share_class": "USDC"})
    assert r2.status_code == 200
    history = r2.json()["history"]
    assert len(history) == 1
    assert history[0]["snapshot_id"] == "snap-webhook-1"


async def test_local_simulated_transfer_adapter_confirms_every_method() -> None:
    """`LocalSimulatedTransferAdapter` (the real `_default_container()` default,
    replacing the old `transfer_adapter=None`) confirms instantly on all three
    `TransferAdapter` Protocol methods — proves it is safe to drive
    `CapitalRouter`/`GracePeriodHandler` end-to-end without a wired
    execution-service integration (out of repo-scope; see the class docstring
    for the T4 no-service-imports HARD RULE this works around)."""

    adapter = LocalSimulatedTransferAdapter()
    withdrawal = await adapter.execute_withdrawal(
        venue="TREASURY", token="USDC", amount=Decimal("10"), to_address="0xDEAD", chain="ETHEREUM"
    )
    internal = await adapter.execute_internal_transfer(
        venue="BINANCE",
        from_wallet="funding",
        to_wallet="trading",
        token="USDT",
        amount=Decimal("5"),
        params={},
    )
    onchain = await adapter.execute_onchain_transfer(
        from_wallet_id="w1", to_address="0xBEEF", token="ETH", amount=Decimal("1"), chain="ETHEREUM"
    )
    for result in (withdrawal, internal, onchain):
        assert result.status is TransferStatus.CONFIRMED
        assert result.transfer_id
        assert result.tx_hash == result.transfer_id
