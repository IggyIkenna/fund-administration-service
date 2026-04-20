#!/usr/bin/env bash
# Per-repo setup — installs the service into a local .venv.
# Invoked by unified-trading-pm/scripts/workspace/setup-workspace-venvs.sh.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# Bootstrap uv + per-repo .venv + editable install.
command -v uv >/dev/null 2>&1 || {
    echo "uv not found — installing via pip" >&2
    pip install uv --quiet
}

[ -d .venv ] || uv venv .venv
# shellcheck disable=SC1091
source .venv/bin/activate

WORKSPACE_ROOT="$(cd "${REPO_ROOT}/.." && pwd)"
for dep in unified-trading-library unified-api-contracts; do
    if [ -d "${WORKSPACE_ROOT}/${dep}" ]; then
        pip install -e "${WORKSPACE_ROOT}/${dep}" --no-deps --quiet || true
    fi
done

uv pip install -e . --quiet || pip install -e . --quiet

echo "fund-administration-service setup complete"
