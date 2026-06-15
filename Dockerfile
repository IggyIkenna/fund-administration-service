# Multi-stage build for fund-administration-service
# Stage 1: Build stage
ARG PROJECT_ID
# Digest-pinned UTL base image (QG STEP 5.79 -- reproducible builds + UTL/UAC provenance).
# Refreshed by the dependency-update fan-out (update-dependency-version.yml) on base-image
# republish; cloudbuild may override at build time: --build-arg BASE_IMAGE_DIGEST=sha256:...
ARG BASE_IMAGE_DIGEST=sha256:a9026757c312fefd4387f353a18723c6aab28c5972403ad0f0b657836091f921
FROM --platform=linux/amd64 asia-northeast1-docker.pkg.dev/${PROJECT_ID}/unified-trading-library/unified-trading-library@${BASE_IMAGE_DIGEST} AS builder

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY README.md ./

COPY fund_administration_service/ ./fund_administration_service/
COPY tests/ ./tests/

# uv >= 0.11 removed --system from `uv sync`.
RUN uv sync --frozen --no-dev

# Stage 2: Runtime stage
ARG PROJECT_ID
FROM --platform=linux/amd64 asia-northeast1-docker.pkg.dev/${PROJECT_ID}/unified-trading-library/unified-trading-library@${BASE_IMAGE_DIGEST}

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY README.md ./

COPY fund_administration_service/ ./fund_administration_service/

# uv >= 0.11 removed --system from `uv sync`; sync into .venv + put it on PATH so the
# `python -m` CMD resolves deps (mirrors alerting-service working pattern).
RUN uv sync --frozen --no-dev
ENV PATH="/app/.venv/bin:${PATH}"

ENV MODE=live
ENV API_HOST=0.0.0.0
ENV API_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
USER appuser

CMD ["python", "-m", "fund_administration_service"]
