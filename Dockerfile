# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.21@sha256:1277da27c2e32bd12cac7fe7ff05f9fd736567ca28a705810df8f7cb3abac20b AS builder

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install dependencies first (better layer caching).
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source and install the project.
COPY src/ ./src/
COPY README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

# Create a non-root user.
RUN groupadd --system robotsix && \
    useradd --system --no-log-init --create-home --gid robotsix robotsix

# Install tini for PID-1 signal handling and zombie reaping.
RUN apt-get update && apt-get install -y --no-install-recommends tini \
    && rm -rf /var/lib/apt/lists/*

LABEL org.opencontainers.image.title="robotsix-agent-comm" \
      org.opencontainers.image.description="Agent communication stack for the robotsix ecosystem" \
      org.opencontainers.image.url="https://github.com/damien-robotsix/robotsix-agent-comm" \
      org.opencontainers.image.source="https://github.com/damien-robotsix/robotsix-agent-comm" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app

# Copy the virtual environment and application code from builder.
COPY --from=builder --chown=robotsix:robotsix /app/.venv ./.venv
COPY --from=builder --chown=robotsix:robotsix /app/src/ ./src/
COPY --from=builder --chown=robotsix:robotsix /app/README.md ./

ENV PATH="/app/.venv/bin:$PATH"

# Drop to non-root.
USER robotsix

EXPOSE 8443

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, ssl; ctx = ssl._create_unverified_context(); urllib.request.urlopen('https://localhost:8443/health', context=ctx)"

ENTRYPOINT ["tini", "--"]
CMD ["robotsix-broker"]
