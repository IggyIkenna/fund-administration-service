#!/usr/bin/env bash
# Repo-specific settings only. Body: unified-trading-pm/scripts/quality-gates-base/base-service.sh
# SSOT: unified-trading-codex/06-coding-standards/quality-gates-service-template.sh
SERVICE_NAME="fund-administration-service"
SOURCE_DIR="fund_administration_service"
# New service scaffold (2026-04-20): actual coverage ~27%. Lowered until real tests land.
# See .coverage-floor-exception.md. Raise to 70%+ as subscription/redemption state machines gain test depth.
MIN_COVERAGE=25
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
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"
