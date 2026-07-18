ARG PROJECT_ID
# Digest-pinned UTL base image (QG STEP 5.79 -- reproducible builds + UTL/UAC provenance).
# Refreshed by the dependency-update fan-out (update-dependency-version.yml) on base-image
# republish; cloudbuild may override at build time: --build-arg BASE_IMAGE_DIGEST=sha256:...
ARG BASE_IMAGE_DIGEST=sha256:07af6d5798b791c525c069481e8aaa77299179ef66ae94a8f5f08c8d067028db
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
RUN uv pip install --system -e . --no-sources

ENV MODE=live
ENV API_HOST=0.0.0.0
ENV API_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
USER appuser

CMD ["python", "-m", "fund_administration_service"]
