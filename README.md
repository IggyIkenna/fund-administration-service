# fund-administration-service

Pooled-fund administration for Investment Management (IM) products.

Owns the subscription, redemption, and allocation lifecycle for pooled funds:

- `AllocatorSubscription` state machine: `PENDING -> APPROVED | REJECTED -> SETTLED`.
- `AllocatorRedemption` state machine: `PENDING -> APPROVED | REJECTED -> PROCESSED -> SETTLED`.
- `FundAllocation` state machine: `PENDING -> IN_PROGRESS -> COMPLETED | FAILED`, driven by `CapitalRouter`.
- Background workers: grace-period handler (redemptions), NAV-strike scheduler.
- REST API (FastAPI) exposes one endpoint per lifecycle transition plus read endpoints.

The service consumes `TransferAdapter` (execution-service) for capital movements and `TreasuryMonitor`
(position-balance-monitor-service) for treasury-vs-trading allocation signals. All domain types flow from
`unified_api_contracts.internal.domain.fund_administration` and `unified_api_contracts.internal`.

See `unified-trading-pm/codex/14-playbooks/shared-core/treasury-and-subaccount-model.md` for the architectural
background and `unified-trading-pm/plans/active/fund_administration_service_and_pooled_subscription_redemption_2026_04_20.md`
for the active rollout plan.

## Local development

```bash
cd fund-administration-service
bash scripts/quality-gates.sh
```

Follow the workspace `CLAUDE.md` conventions: `uv pip install` not `pip install`, quality-gates via the per-repo
`.venv` (never `pytest` directly), `basedpyright` not `pyright`.
