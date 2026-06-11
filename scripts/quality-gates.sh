#!/usr/bin/env bash
# Repo-specific settings only. Body: unified-trading-pm/scripts/quality-gates-base/base-service.sh
# SSOT: unified-trading-codex/06-coding-standards/quality-gates-service-template.sh
SERVICE_NAME="fund-administration-service"
SOURCE_DIR="fund_administration_service"
# After UAC FundTransferContext lifting (2026-04-22), 3 previously-broken test modules
# (test_api_end_to_end, test_background_handlers, test_capital_router) collect + pass — actual
# coverage is now ~84%. Restored to system floor of 70.
MIN_COVERAGE=70
RUN_INTEGRATION=false
PYTEST_WORKERS=${PYTEST_WORKERS:-2}
LOCAL_DEPS=()

# Manifest entry landed 2026-04-20 in unified-trading-pm/workspace-manifest.json
# — manifest-alignment scanner unblocked. Schema-provenance scanner: every
# BaseModel/dataclass is either imported from UAC or tagged
# SCHEMA_PROVENANCE_EXEMPT (API request bodies + internal DI container — not
# domain messages). Follow-up: promote FundTransferContext to UAC to eliminate
# the last SCHEMA_PROVENANCE_EXEMPT tag in allocation/transfer_protocol.py.

WORKSPACE_ROOT="$(cd "$(git rev-parse --show-toplevel)/.." && pwd)"
# CODEX_MAX_VIOLATIONS pinned 2026-06-11 per plans/active/codex_violations_ratchet_to_five_2026_06_10.md (census-honest: 0 current violations; ratchet-down only).
CODEX_MAX_VIOLATIONS=0
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"
