#!/usr/bin/env bash
# Repo-specific settings only. Body: unified-trading-pm/scripts/quality-gates-base/base-service.sh
# SSOT: unified-trading-codex/06-coding-standards/quality-gates-service-template.sh
SERVICE_NAME="fund-administration-service"
SOURCE_DIR="fund_administration_service"
# New service scaffold: 70% floor starting baseline.
MIN_COVERAGE=70
RUN_INTEGRATION=false
PYTEST_WORKERS=${PYTEST_WORKERS:-2}
LOCAL_DEPS=()

# Phase-2 scaffold: repo is not yet registered in workspace-manifest.json
# (workspace-manifest.json edit is a separate follow-up per plan Phase 2).
# Schema-provenance + manifest-alignment scanners require a manifest entry,
# so skip until the follow-up lands. Every BaseModel/dataclass in this repo
# is either imported from UAC or is tagged SCHEMA_PROVENANCE_EXEMPT (API
# request bodies + internal DI container — not domain messages).
SCHEMA_PROVENANCE_SKIP=true
export SCHEMA_PROVENANCE_SKIP
MANIFEST_ALIGNMENT_SKIP=true
export MANIFEST_ALIGNMENT_SKIP

WORKSPACE_ROOT="$(cd "$(git rev-parse --show-toplevel)/.." && pwd)"
source "${WORKSPACE_ROOT}/unified-trading-pm/scripts/quality-gates-base/base-service.sh"
