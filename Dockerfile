FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/

WORKDIR /app
COPY pyproject.toml uv.lock README.md ./
COPY src/ src/

RUN uv pip install --system --no-cache .

# Only meaningful for network transports (http/sse) where the server binds a
# port; harmless no-op for stdio use. No curl in this base image, so use the
# stdlib instead of adding a dependency just for the healthcheck.
HEALTHCHECK --interval=30s --timeout=3s --start-period=60s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=2)" || exit 1

ENTRYPOINT ["obsidian-remote-mcp"]
