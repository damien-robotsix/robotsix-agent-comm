# syntax=docker/dockerfile:1

FROM python:3.12-slim

# Create a non-root user.
RUN groupadd --system robotsix && \
    useradd --system --no-log-init --create-home --gid robotsix robotsix

WORKDIR /app

# Install uv.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Install dependencies first (better layer caching).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Copy source.
COPY src/ ./src/
COPY README.md ./

# Install the project itself.
RUN uv sync --frozen --no-dev

# Drop to non-root.
USER robotsix

EXPOSE 8443

# Run the broker via the console script.
CMD ["uv", "run", "robotsix-broker"]
