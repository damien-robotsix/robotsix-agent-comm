# syntax=docker/dockerfile:1

FROM ghcr.io/astral-sh/uv:0.11.21@sha256:1277da27c2e32bd12cac7fe7ff05f9fd736567ca28a705810df8f7cb3abac20b AS builder

WORKDIR /app

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

WORKDIR /app

# Copy the virtual environment and application code from builder.
COPY --from=builder /app/.venv ./.venv
COPY --from=builder /app/src/ ./src/
COPY --from=builder /app/README.md ./

ENV PATH="/app/.venv/bin:$PATH"

# Drop to non-root.
USER robotsix

EXPOSE 8443

# Run the broker via the installed console script.
CMD ["robotsix-broker"]
