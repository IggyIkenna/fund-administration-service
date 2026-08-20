# Local ``TransferAdapter`` Protocol + ``TransferStatus`` / ``TransferResult``
# structural mirrors of execution-service types.
#
# The execution-service canonical ``TransferAdapter`` Protocol + ``TransferStatus``
# / ``TransferResult`` dataclasses ship in
# ``execution_service.engine.transfers.adapter``. Taking a hard dependency on
# execution-service would pull in its entire algorithm / DeFi / venue-adapter
# graph — too heavy for fund-administration-service tests. Instead we declare a
# narrow structural mirror here: any execution-service adapter
# (MockTransferAdapter, LiveCcxtTransferAdapter, LiveCustodyTransferAdapter,
# CompositeTransferAdapter) satisfies this ``TransferAdapter`` Protocol at
# runtime by duck-typing, so the CapitalRouter / GracePeriodHandler still
# route through the canonical execution-service implementations in production.
#
# Structural subtyping is the right seam here: the wire protocol between
# fund-administration-service and execution-service is declared once in
# execution-service, and this Protocol is a runtime-compatible narrow view.
#
# ``FundTransferContext`` is NOT mirrored — it is a pure data class with no
# Protocol semantics, and UAC owns it as a first-class fund-administration
# domain type (``unified_api_contracts.fund_administration.FundTransferContext``).
# Consumers should import it directly from UAC.
"""Local transfer-adapter Protocol + ``TransferResult`` / ``TransferStatus`` mirrors.

``FundTransferContext`` is re-exported from UAC (the canonical SSOT) — this
module used to host a byte-for-byte duplicate dataclass; the duplicate has
been deleted as part of the UAC-promotion fix. Consumers should import from
``unified_api_contracts`` directly where possible.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol

from unified_api_contracts import FundTransferContext as FundTransferContext


class TransferStatus(StrEnum):
    """Mirrors ``execution_service.engine.transfers.adapter.TransferStatus``."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class TransferResult:
    """Mirrors ``execution_service.engine.transfers.adapter.TransferResult``."""

    transfer_id: str
    status: TransferStatus
    tx_hash: str = ""
    amount_transferred: Decimal = Decimal("0")
    fee_paid: Decimal = Decimal("0")
    confirmations: int = 0
    error: str | None = None
    fund_context: FundTransferContext | None = None


class TransferAdapter(Protocol):
    """Narrow structural view over execution-service ``TransferAdapter``."""

    async def execute_internal_transfer(
        self,
        venue: str,
        from_wallet: str,
        to_wallet: str,
        token: str,
        amount: Decimal,
        params: dict[str, str],
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult: ...

    async def execute_withdrawal(
        self,
        venue: str,
        token: str,
        amount: Decimal,
        to_address: str,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult: ...

    async def execute_onchain_transfer(
        self,
        from_wallet_id: str,
        to_address: str,
        token: str,
        amount: Decimal,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult: ...


class LocalSimulatedTransferAdapter:
    """Default production ``TransferAdapter`` — same-repo, no cross-service call.

    **Why this exists instead of execution-service's real ``CompositeTransferAdapter``**
    (confirmed 2026-08-20): fund-administration-service and execution-service are
    BOTH T4 services under the workspace's 5-tier dependency model — "no
    service↔service imports" is a HARD RULE
    (``codex/04-architecture/tier-and-import-architecture.md`` rule 2/5),
    so importing ``execution_service.engine.transfers`` directly here would be
    a review-blocking violation. execution-service also does not currently
    expose a synchronous REST contract fund-administration-service could call
    instead — its only transfer-related route is the read-only
    ``GET /transfers/active`` monitor. Building that cross-repo contract is
    out of fund-administration-service's repo scope for this plan.

    This adapter is the honest interim default: it simulates immediate
    confirmation (mirroring the ``MockTransferAdapter`` "instant success"
    convention execution-service itself documents for tests/backtest) so the
    redemption/rebalance pipelines are genuinely exercised end-to-end rather
    than permanently 503'ing on a wired-but-unusable ``None``. The real
    custody/on-chain settlement leg is
    ``redemption_wallet_transfer_execution_2026_08_20.md`` (companion plan) —
    its own tests inject a real adapter directly, independent of this DI seam.
    """

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
        return self._confirmed(amount, fund_context)

    async def execute_withdrawal(
        self,
        venue: str,
        token: str,
        amount: Decimal,
        to_address: str,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return self._confirmed(amount, fund_context)

    async def execute_onchain_transfer(
        self,
        from_wallet_id: str,
        to_address: str,
        token: str,
        amount: Decimal,
        chain: str,
        fund_context: FundTransferContext | None = None,
    ) -> TransferResult:
        return self._confirmed(amount, fund_context)

    @staticmethod
    def _confirmed(amount: Decimal, fund_context: FundTransferContext | None) -> TransferResult:
        transfer_id = f"local-sim-{uuid.uuid4().hex[:12]}"
        return TransferResult(
            transfer_id=transfer_id,
            status=TransferStatus.CONFIRMED,
            tx_hash=transfer_id,
            amount_transferred=amount,
            fund_context=fund_context,
        )
