# SCHEMA_PROVENANCE_EXEMPT — local transfer-adapter Protocol + routing context.
#
# The execution-service canonical ``TransferAdapter`` Protocol + ``FundTransferContext``
# dataclass ship in ``execution_service.engine.transfers.adapter``. Taking a hard
# dependency on execution-service would pull in its entire algorithm / DeFi /
# venue-adapter graph — too heavy for fund-administration-service tests. Instead
# we declare a narrow structural mirror here: any execution-service adapter
# (MockTransferAdapter, LiveCcxtTransferAdapter, LiveCustodyTransferAdapter,
# CompositeTransferAdapter) satisfies this ``TransferAdapter`` Protocol at runtime
# by duck-typing, so the CapitalRouter / GracePeriodHandler still route through
# the canonical execution-service implementations in production.
#
# Structural subtyping is the right seam here: the wire protocol between
# fund-administration-service and execution-service is declared once in
# execution-service, and this Protocol is a runtime-compatible narrow view.
"""Local transfer-adapter Protocol + ``FundTransferContext`` mirror."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class TransferStatus(StrEnum):
    """Mirrors ``execution_service.engine.transfers.adapter.TransferStatus``."""

    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"


@dataclass(frozen=True)
class FundTransferContext:
    """Routing context tagged onto every transfer driven by fund-administration.

    Structural mirror of ``execution_service.engine.transfers.adapter.FundTransferContext``.
    The execution-service adapter accepts this by keyword and propagates the
    values through logs, events, and the ``TransferResult.fund_context`` field.
    """

    fund_id: str
    share_class: str
    allocation_id: str | None = None


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
