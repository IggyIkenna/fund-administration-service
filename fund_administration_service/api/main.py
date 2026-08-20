# SCHEMA_PROVENANCE_EXEMPT — remaining dataclasses are private DI-container
# + AML-KYC decision types used purely inside create_app(); they are NOT
# domain contracts (those live in UAC and are imported above).
"""Fund administration FastAPI app.

Exposes one endpoint per lifecycle transition plus read endpoints. Every
request body and every domain payload is a UAC-defined model — the service
source declares no local BaseModel subclasses. State machines in
``subscription.state_machine`` and ``redemption.state_machine`` do the
actual transition work.

Dependency wiring is deliberate: a single container dataclass bound at
``create_app()`` time. Tests replace the dependencies wholesale by passing
their own ``_Container`` instance into ``create_app(...)``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from fastapi import FastAPI, HTTPException
from unified_api_contracts import (
    AllocationExecutionStatus,
    AllocatorRedemption,
    AllocatorSubscription,
    ApproveSubscriptionRequest,
    FeeStructure,
    FundAllocation,
    FundNAVSnapshot,
    ProcessRedemptionRequest,
    RebalanceRequest,
    RedeemRequest,
    RedemptionStatus,
    RejectRequest,
    SubscribeRequest,
    SubscriptionStatus,
)
from unified_trading_library import (
    FUND_ALLOCATION_REBALANCED,
    REDEMPTION_APPROVED,
    REDEMPTION_PROCESSED,
    REDEMPTION_REJECTED,
    REDEMPTION_REQUESTED,
    REDEMPTION_SETTLED,
    SUBSCRIPTION_APPROVED,
    SUBSCRIPTION_REJECTED,
    SUBSCRIPTION_REQUESTED,
    SUBSCRIPTION_SETTLED,
    make_health_router,
)

from fund_administration_service import __version__
from fund_administration_service.allocation import AllocationTarget, CapitalRouter
from fund_administration_service.allocation.transfer_protocol import TransferAdapter
from fund_administration_service.background import GracePeriodHandler, NAVStrikeScheduler
from fund_administration_service.config import (
    FundAdministrationServiceConfig,
    get_service_config,
)
from fund_administration_service.events import emit_fund_admin_event
from fund_administration_service.persistence import InMemoryStore, PersistenceStore
from fund_administration_service.redemption import (
    approve_redemption,
    create_redemption,
    process_redemption,
    reject_redemption,
    settle_redemption,
)
from fund_administration_service.subscription import (
    AmlKycDecision,
    AmlKycGate,
    NavProvider,
    approve_subscription,
    create_subscription,
    reject_subscription,
    settle_subscription,
)

logger = logging.getLogger(__name__)


@dataclass
class _AutoApproveDecision:
    approved: bool
    reason: str


class _AutoApproveGate:
    """Default ``AmlKycGate`` used in dev/test — always approves."""

    def evaluate(self, allocator_id: str, fund_id: str, share_class: str) -> AmlKycDecision:
        return cast(
            AmlKycDecision,
            _AutoApproveDecision(approved=True, reason="auto-approve (scaffold)"),
        )


class _EmptyNavProvider:
    """Placeholder ``NavProvider`` that returns ``None`` until wired to GCS."""

    def latest_snapshot(self, fund_id: str, share_class: str) -> FundNAVSnapshot | None:
        return None


@dataclass
class _Container:
    """DI container — bound at ``create_app`` time, swapped in tests."""

    service_config: FundAdministrationServiceConfig
    store: PersistenceStore
    nav_provider: NavProvider
    aml_gate: AmlKycGate
    transfer_adapter: TransferAdapter | None
    fee_structure_for_fund: dict[str, FeeStructure]
    last_nav_strike: dict[str, datetime]


# Note: request-body schemas (SubscribeRequest, RedeemRequest,
# ApproveSubscriptionRequest, ProcessRedemptionRequest, RejectRequest,
# RebalanceRequest) live in UAC — imported above from unified_api_contracts.


# ---------- Factory ----------


def _build_default_container() -> _Container:
    return _Container(
        service_config=get_service_config(),
        store=InMemoryStore(),
        nav_provider=_EmptyNavProvider(),
        aml_gate=_AutoApproveGate(),
        transfer_adapter=None,
        fee_structure_for_fund={},
        last_nav_strike={},
    )


def _data_freshness_from(container: _Container) -> dict[str, object]:
    """Report the most recent NAV strike per fund as the service's freshness signal."""

    if not container.last_nav_strike:
        return {"last_processed_date": None, "stale": True}
    latest = max(container.last_nav_strike.values())
    return {
        "last_processed_date": latest.date().isoformat(),
        "stale": False,
    }


def _make_lifespan(ctx: _Container) -> Callable[[FastAPI], AbstractAsyncContextManager[None]]:
    """Builds the lifespan context manager closed over *ctx* (avoids the
    untyped ``app.state`` bag — ``ctx`` is captured by closure instead).

    Starts the redemption-cadence + NAV-strike wall-clock loops at boot.
    ``GracePeriodHandler.run_forever()`` drives APPROVED redemptions to
    PROCESSED on ``redemption_cadence_seconds``; ``NAVStrikeScheduler.run_forever()``
    strikes NAV on ``nav_publish_cadence_seconds``. Both are cancelled cleanly
    on shutdown. Previously neither loop was ever started outside unit tests.
    """

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        tasks: list[asyncio.Task[None]] = []
        # Only start the redemption-cadence loop when a real TransferAdapter
        # is wired — mock/test containers may pass None explicitly, and there
        # is nothing useful for GracePeriodHandler to drive without one.
        transfer_adapter = ctx.transfer_adapter
        if transfer_adapter is not None:
            tasks.append(
                asyncio.create_task(
                    GracePeriodHandler(
                        service_config=ctx.service_config,
                        store=ctx.store,
                        nav_provider=ctx.nav_provider,
                        fee_structure_for_fund=ctx.fee_structure_for_fund,
                        transfer_adapter=transfer_adapter,
                    ).run_forever(ctx.service_config.redemption_cadence_seconds)
                )
            )
        tasks.append(
            asyncio.create_task(
                NAVStrikeScheduler(
                    service_config=ctx.service_config,
                    nav_provider=ctx.nav_provider,
                ).run_forever(ctx.service_config.nav_publish_cadence_seconds)
            )
        )
        try:
            yield
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with suppress(asyncio.CancelledError):
                    await task

    return _lifespan


def create_app(container: _Container | None = None) -> FastAPI:
    """Build the FastAPI application.

    Args:
        container: Optional DI container for tests. When ``None`` a default
            in-memory container is built from ``get_service_config()``.
    """

    ctx = container or _build_default_container()
    app = FastAPI(
        title="fund-administration-service",
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=_make_lifespan(ctx),
    )
    app.include_router(
        make_health_router(
            service_name="fund-administration-service",
            version=__version__,
            data_freshness=lambda: _data_freshness_from(ctx),
        )
    )
    _register_subscription_routes(app, ctx)
    _register_redemption_routes(app, ctx)
    _register_allocation_routes(app, ctx)
    return app


# ---------- Subscription routes ----------


def _register_subscription_routes(app: FastAPI, ctx: _Container) -> None:
    def post_subscription(body: SubscribeRequest) -> AllocatorSubscription:
        sub = create_subscription(
            subscription_id=body.subscription_id,
            fund_id=body.fund_id,
            allocator_id=body.allocator_id,
            share_class=body.share_class,
            requested_amount_usd=body.requested_amount_usd,
        )
        ctx.store.put_subscription(sub)
        emit_fund_admin_event(
            SUBSCRIPTION_REQUESTED,
            details={
                "subscription_id": sub.subscription_id,
                "fund_id": sub.fund_id,
                "allocator_id": sub.allocator_id,
                "share_class": sub.share_class,
                "requested_amount_usd": str(sub.requested_amount_usd),
            },
        )
        return sub

    def get_subscription(subscription_id: str) -> AllocatorSubscription:
        sub = ctx.store.get_subscription(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        return sub

    def approve_sub(
        subscription_id: str, body: ApproveSubscriptionRequest
    ) -> AllocatorSubscription:
        sub = ctx.store.get_subscription(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        if sub.status is not SubscriptionStatus.PENDING:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot APPROVE from status={sub.status.value}",
            )
        decision = ctx.aml_gate.evaluate(sub.allocator_id, sub.fund_id, sub.share_class)
        if not decision.approved:
            rejected = reject_subscription(sub, reason=decision.reason)
            ctx.store.put_subscription(rejected)
            emit_fund_admin_event(
                SUBSCRIPTION_REJECTED,
                details={
                    "subscription_id": rejected.subscription_id,
                    "fund_id": rejected.fund_id,
                    "reason": decision.reason,
                },
                severity="WARN",
            )
            return rejected
        snapshot = ctx.nav_provider.latest_snapshot(sub.fund_id, sub.share_class)
        if snapshot is None:
            raise HTTPException(
                status_code=409,
                detail="No NAV snapshot available — cannot resolve NAV strike",
            )
        approved = approve_subscription(sub, snapshot, body.nav_per_unit)
        ctx.store.put_subscription(approved)
        emit_fund_admin_event(
            SUBSCRIPTION_APPROVED,
            details={
                "subscription_id": approved.subscription_id,
                "fund_id": approved.fund_id,
                "units_issued": str(approved.units_issued),
                "nav_strike_snapshot_id": approved.nav_strike_snapshot_id,
            },
        )
        return approved

    def reject_sub(subscription_id: str, body: RejectRequest) -> AllocatorSubscription:
        sub = ctx.store.get_subscription(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        rejected = reject_subscription(sub, reason=body.reason)
        ctx.store.put_subscription(rejected)
        emit_fund_admin_event(
            SUBSCRIPTION_REJECTED,
            details={
                "subscription_id": rejected.subscription_id,
                "fund_id": rejected.fund_id,
                "reason": body.reason,
            },
            severity="WARN",
        )
        return rejected

    def settle_sub(subscription_id: str) -> AllocatorSubscription:
        sub = ctx.store.get_subscription(subscription_id)
        if sub is None:
            raise HTTPException(status_code=404, detail="Subscription not found")
        settled = settle_subscription(sub)
        ctx.store.put_subscription(settled)
        emit_fund_admin_event(
            SUBSCRIPTION_SETTLED,
            details={
                "subscription_id": settled.subscription_id,
                "fund_id": settled.fund_id,
                "units_issued": str(settled.units_issued),
            },
        )
        return settled

    app.add_api_route("/subscriptions", post_subscription, methods=["POST"])
    app.add_api_route("/subscriptions/{subscription_id}", get_subscription, methods=["GET"])
    app.add_api_route("/subscriptions/{subscription_id}/approve", approve_sub, methods=["POST"])
    app.add_api_route("/subscriptions/{subscription_id}/reject", reject_sub, methods=["POST"])
    app.add_api_route("/subscriptions/{subscription_id}/settle", settle_sub, methods=["POST"])


# ---------- Redemption routes ----------


def _register_redemption_routes(app: FastAPI, ctx: _Container) -> None:
    def post_redemption(body: RedeemRequest) -> AllocatorRedemption:
        grace = (
            body.grace_period_days
            if body.grace_period_days is not None
            else ctx.service_config.default_grace_period_days
        )
        red = create_redemption(
            redemption_id=body.redemption_id,
            fund_id=body.fund_id,
            allocator_id=body.allocator_id,
            share_class=body.share_class,
            units_to_redeem=body.units_to_redeem,
            destination=body.destination,
            grace_period_days=grace,
        )
        ctx.store.put_redemption(red)
        emit_fund_admin_event(
            REDEMPTION_REQUESTED,
            details={
                "redemption_id": red.redemption_id,
                "fund_id": red.fund_id,
                "allocator_id": red.allocator_id,
                "share_class": red.share_class,
                "units_to_redeem": str(red.units_to_redeem),
                "grace_period_days": red.grace_period_days,
            },
        )
        return red

    def get_redemption(redemption_id: str) -> AllocatorRedemption:
        red = ctx.store.get_redemption(redemption_id)
        if red is None:
            raise HTTPException(status_code=404, detail="Redemption not found")
        return red

    def approve_red(redemption_id: str) -> AllocatorRedemption:
        red = ctx.store.get_redemption(redemption_id)
        if red is None:
            raise HTTPException(status_code=404, detail="Redemption not found")
        approved = approve_redemption(red)
        ctx.store.put_redemption(approved)
        emit_fund_admin_event(
            REDEMPTION_APPROVED,
            details={
                "redemption_id": approved.redemption_id,
                "fund_id": approved.fund_id,
                "grace_period_days": approved.grace_period_days,
            },
        )
        return approved

    def reject_red(redemption_id: str, body: RejectRequest) -> AllocatorRedemption:
        red = ctx.store.get_redemption(redemption_id)
        if red is None:
            raise HTTPException(status_code=404, detail="Redemption not found")
        rejected = reject_redemption(red, reason=body.reason)
        ctx.store.put_redemption(rejected)
        emit_fund_admin_event(
            REDEMPTION_REJECTED,
            details={
                "redemption_id": rejected.redemption_id,
                "fund_id": rejected.fund_id,
                "reason": body.reason,
            },
            severity="WARN",
        )
        return rejected

    def process_red(redemption_id: str, body: ProcessRedemptionRequest) -> AllocatorRedemption:
        red = ctx.store.get_redemption(redemption_id)
        if red is None:
            raise HTTPException(status_code=404, detail="Redemption not found")
        if red.status is not RedemptionStatus.APPROVED:
            raise HTTPException(
                status_code=409,
                detail=f"Cannot PROCESS from status={red.status.value}",
            )
        snapshot = ctx.nav_provider.latest_snapshot(red.fund_id, red.share_class)
        if snapshot is None:
            raise HTTPException(
                status_code=409,
                detail="No NAV snapshot available — cannot settle redemption",
            )
        fee_structure = ctx.fee_structure_for_fund.get(red.fund_id)
        if fee_structure is None:
            raise HTTPException(
                status_code=409,
                detail=f"No FeeStructure registered for fund={red.fund_id}",
            )
        processed = process_redemption(
            red,
            settlement_snapshot=snapshot,
            settlement_nav=body.settlement_nav,
            fee_structure=fee_structure,
            settlement_reference=body.settlement_reference,
        )
        ctx.store.put_redemption(processed)
        emit_fund_admin_event(
            REDEMPTION_PROCESSED,
            details={
                "redemption_id": processed.redemption_id,
                "fund_id": processed.fund_id,
                "cash_amount_due_usd": str(processed.cash_amount_due_usd),
                "settlement_reference": processed.settlement_reference,
            },
        )
        return processed

    def settle_red(redemption_id: str) -> AllocatorRedemption:
        red = ctx.store.get_redemption(redemption_id)
        if red is None:
            raise HTTPException(status_code=404, detail="Redemption not found")
        settled = settle_redemption(red)
        ctx.store.put_redemption(settled)
        emit_fund_admin_event(
            REDEMPTION_SETTLED,
            details={
                "redemption_id": settled.redemption_id,
                "fund_id": settled.fund_id,
            },
        )
        return settled

    app.add_api_route("/redemptions", post_redemption, methods=["POST"])
    app.add_api_route("/redemptions/{redemption_id}", get_redemption, methods=["GET"])
    app.add_api_route("/redemptions/{redemption_id}/approve", approve_red, methods=["POST"])
    app.add_api_route("/redemptions/{redemption_id}/reject", reject_red, methods=["POST"])
    app.add_api_route("/redemptions/{redemption_id}/process", process_red, methods=["POST"])
    app.add_api_route("/redemptions/{redemption_id}/settle", settle_red, methods=["POST"])


# ---------- Allocation / NAV routes ----------


def _register_allocation_routes(app: FastAPI, ctx: _Container) -> None:
    def list_allocations(fund_id: str) -> list[FundAllocation]:
        return ctx.store.list_allocations_for_fund(fund_id)

    async def rebalance(fund_id: str, body: RebalanceRequest) -> list[FundAllocation]:
        if ctx.transfer_adapter is None:
            raise HTTPException(
                status_code=503,
                detail="TransferAdapter not wired — rebalance unavailable",
            )
        targets: list[AllocationTarget] = []
        for raw in body.targets:
            targets.append(
                AllocationTarget(
                    allocation_id=str(raw["allocation_id"]),
                    strategy_id=str(raw["strategy_id"]),
                    target_amount_usd=Decimal(str(raw["target_amount_usd"])),
                    venue=str(raw["venue"]),
                    from_wallet=str(raw["from_wallet"]),
                    to_wallet=str(raw["to_wallet"]),
                    token=str(raw["token"]),
                )
            )
        capital_router = CapitalRouter(
            transfer_adapter=ctx.transfer_adapter,
            store=ctx.store,
        )
        allocations = await capital_router.rebalance(
            fund_id=fund_id, share_class=body.share_class, targets=targets
        )
        # Emit a single final FUND_ALLOCATION_REBALANCED summary alongside the
        # per-transfer events already fired by CapitalRouter.
        emit_fund_admin_event(
            FUND_ALLOCATION_REBALANCED,
            details={
                "fund_id": fund_id,
                "share_class": body.share_class,
                "allocations_completed": sum(
                    1
                    for a in allocations
                    if a.execution_status is AllocationExecutionStatus.COMPLETED
                ),
                "allocations_failed": sum(
                    1 for a in allocations if a.execution_status is AllocationExecutionStatus.FAILED
                ),
            },
        )
        ctx.last_nav_strike[fund_id] = datetime.now(UTC)
        return allocations

    def nav_history(fund_id: str, share_class: str) -> dict[str, object]:
        snapshot = ctx.nav_provider.latest_snapshot(fund_id, share_class)
        if snapshot is None:
            return {"fund_id": fund_id, "share_class": share_class, "history": []}
        return {
            "fund_id": fund_id,
            "share_class": share_class,
            "history": [snapshot.model_dump(mode="json")],
        }

    app.add_api_route("/funds/{fund_id}/allocations", list_allocations, methods=["GET"])
    app.add_api_route("/funds/{fund_id}/allocations/rebalance", rebalance, methods=["POST"])
    app.add_api_route("/funds/{fund_id}/nav/history", nav_history, methods=["GET"])
