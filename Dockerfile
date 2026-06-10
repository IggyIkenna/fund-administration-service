# Multi-stage build for fund-administration-service
# Stage 1: Build stage
ARG PROJECT_ID
# Digest-pinned UTL base image (QG STEP 5.79 -- reproducible builds + UTL/UAC provenance).
# Refreshed by the dependency-update fan-out (update-dependency-version.yml) on base-image
# republish; cloudbuild may override at build time: --build-arg BASE_IMAGE_DIGEST=sha256:...
ARG BASE_IMAGE_DIGEST=sha256:058d589f67d4d3ed3163484de40fd2eba9adfef7e8a7e707239293868b0197f4
FROM --platform=linux/amd64 asia-northeast1-docker.pkg.dev/${PROJECT_ID}/unified-trading-library/unified-trading-library@${BASE_IMAGE_DIGEST} AS builder

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY README.md ./

COPY fund_administration_service/ ./fund_administration_service/
COPY tests/ ./tests/

RUN uv sync --frozen --no-dev --system

# Stage 2: Runtime stage
ARG PROJECT_ID
FROM --platform=linux/amd64 asia-northeast1-docker.pkg.dev/${PROJECT_ID}/unified-trading-library/unified-trading-library@${BASE_IMAGE_DIGEST}

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY README.md ./

COPY fund_administration_service/ ./fund_administration_service/

RUN uv sync --frozen --no-dev --system

ENV MODE=live
ENV API_HOST=0.0.0.0
ENV API_PORT=8080

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl -f http://localhost:8080/health || exit 1

RUN addgroup --system appuser && adduser --system --ingroup appuser appuser
USER appuser

CMD ["python", "-m", "fund_administration_service"]
