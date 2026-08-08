ARG PROJECT_ID
# Digest-pinned UTL base image (QG STEP 5.79 -- reproducible builds + UTL/UAC provenance).
# Refreshed by the dependency-update fan-out (update-dependency-version.yml) on base-image
# republish; cloudbuild may override at build time: --build-arg BASE_IMAGE_DIGEST=sha256:...
ARG BASE_IMAGE_DIGEST=sha256:317a56ddff5b1d3aa156ebeeec4f59e8561393d6400534a0f5d2e21b6569c28b
FROM --platform=linux/amd64 asia-northeast1-docker.pkg.dev/${PROJECT_ID}/unified-trading-library/unified-trading-library@${BASE_IMAGE_DIGEST}

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY README.md ./

COPY fund_administration_service/ ./fund_administration_service/
# Copy the repo's own scripts/ so the in-image quality-gates (cloudbuild Step #6) runs THIS service's
# GUARDED quality-gates.sh — without it the image falls through to the base image's leftover library
# QG (sources base-library.sh unguarded → "line 101: //unified-trading-pm/.../base-library.sh" fail).
COPY scripts/ ./scripts/

# Install service + external deps into system python, ignoring [tool.uv.sources] editable sibling
# paths (--no-sources): UTL/UAC are in the base image; the GCP build context has no sibling repos.
# (--system installs into system python, so no .venv on PATH is needed — mirrors mdps.)
# scm-version-fix: pretend version for editable install (D13 git-tag versioning)
ARG SETUPTOOLS_SCM_PRETEND_VERSION=0.0.0
ENV SETUPTOOLS_SCM_PRETEND_VERSION=${SETUPTOOLS_SCM_PRETEND_VERSION}

# uv does NOT read pip.conf's extra-index-url (pip-only convention) and this Dockerfile carries
# no pip.conf at all, so uv has ZERO private-registry config — any dependency floor-bump past what
# the pinned base image bundles (e.g. unified-trading-library) reads as "not found in the package
# registry" with no auth error surfaced. See
# cloud_build_unified_api_contracts_publish_ordering_race_2026_07_29.md (instruments-service root
# cause + fix, commits 76eba912/4c05f2d3). Fix: mount a freshly-minted access token (same
# auth-precheck mechanism already proven against this exact index) as a BuildKit secret, scoped to
# only this RUN layer — never baked into an image layer or history.
# Retry-with-backoff (3 attempts, ~45s total budget): hardens against the exact
# publish-ordering-race window this doc tracks recurring on the next cross-repo floor-bump.
RUN --mount=type=secret,id=gar_token \
    UV_EXTRA_INDEX_URL="https://oauth2accesstoken:$(cat /run/secrets/gar_token)@asia-northeast1-python.pkg.dev/central-element-323112/unified-libraries/simple/" \
    sh -c 'i=1; until uv pip install --system -e . --no-sources; do [ "$i" -ge 3 ] && { echo "uv pip install failed after 3 attempts" >&2; exit 1; }; w=$((15 * i)); echo "uv pip install failed (attempt $i/3) -- retrying in ${w}s"; sleep "$w"; i=$((i + 1)); done'

ENV MODE=live
ENV API_HOST=0.0.0.0
ENV API_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
USER appuser

CMD ["python", "-m", "fund_administration_service"]
