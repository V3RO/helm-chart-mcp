FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY README.md ./

RUN uv build

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash mcp

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY --from=builder /app/dist/*.whl /tmp/

RUN uv pip install --system /tmp/*.whl && rm /tmp/*.whl

USER mcp
WORKDIR /home/mcp

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import helm_chart_mcp; print('OK')" || exit 1

ENTRYPOINT ["helm-chart-mcp"]