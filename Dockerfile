FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install uv && uv pip install --system .
COPY src/ src/
ENTRYPOINT ["python", "-m", "second_brain_mcp.server"]
