FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

RUN uv pip install --system --no-cache .

ENTRYPOINT ["obsidian-remote-mcp"]
