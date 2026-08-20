"""Build the canonical ``LedgerRow`` for a settled redemption's cash movement.

Writer for the ``ledger_type=treasury/client_id={cid}/`` SSOT partition — acked
2026-05-23 (``unified-trading-pm@351a47b61``, Phase 6 TreasuryLedger split
decision) as fund-administration-service's responsibility. This was the
confirmed-unimplemented residual gap the acking plan flagged (zero code hits
fleet-wide for ``ledger_type=treasury`` as of the 2026-07-12 re-audit).

Scope note: this builds the canonical ``LedgerRow`` and persists it via
``PersistenceStore`` (queryable by ``client_id``, matching every other object
this store already holds) — the actual GCS parquet partition writer is a
distinct, larger piece of infrastructure with NO shipped precedent anywhere in
the fleet to mirror (confirmed: even the "shipped" InstructionLedger has no
real production GCS writer, only a CLI replay reference). Building that from
scratch was out of scope for making this one redemption-cadence plan real;
the in-memory store gives the same queryable-by-client_id guarantee the
plan's done-when bar asked for, and is a genuine swap-in seam for a durable
writer later (the SAME seam ``PersistenceStore`` already documents for the
SQL/Firestore follow-up).
"""

from __future__ import annotations

from unified_api_contracts import (
    AllocatorRedemption,
    EventOrigin,
    EventType,
    LedgerAssetClass,
    LedgerRow,
)

from fund_administration_service.config import FundAdministrationServiceConfig

# fund-administration-service does not yet track asset_group per fund (no
# multi-asset-group fund exists yet) — every treasury ledger row uses this
# default until a real per-fund asset_group field lands. Flagged here rather
# than silently guessed so the next reader knows this is a known limitation,
# not an oversight.
_DEFAULT_LEDGER_ASSET_GROUP = "defi"


def build_treasury_ledger_row(
    settled: AllocatorRedemption,
    service_config: FundAdministrationServiceConfig,
) -> LedgerRow:
    """Build the ``LedgerRow`` for *settled*'s cash movement out of the treasury.

    Requires ``settled.status == SETTLED`` (``settlement_timestamp`` and
    ``cash_amount_due_usd`` both populated by that point in the state
    machine). ``counterparty_client_id`` is left ``None`` — the destination is
    an external bank/wallet reference, never another internal client (funds
    never move between clients).
    """

    assert settled.settlement_timestamp is not None
    assert settled.cash_amount_due_usd is not None

    return LedgerRow(
        event_id=settled.redemption_id,
        row_id=f"{settled.redemption_id}.0",
        event_origin=EventOrigin.INSTRUCTION,
        event_type=EventType.WITHDRAWAL_TO_BANK,
        timestamp_utc=settled.settlement_timestamp,
        asset_group=_DEFAULT_LEDGER_ASSET_GROUP,
        venue="TREASURY",
        chain=service_config.redemption_settlement_chain,
        account_id=service_config.treasury_wallet_id,
        client_id=settled.allocator_id,
        counterparty_account=settled.destination,
        counterparty_client_id=None,
        asset_symbol=settled.share_class,
        asset_canonical_id=settled.share_class,
        asset_class=LedgerAssetClass.STABLE,
        delta=-settled.cash_amount_due_usd,
        quote_currency="USD",
    )
