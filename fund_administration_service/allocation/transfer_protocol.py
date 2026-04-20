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
