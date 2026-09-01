from pathlib import Path

import yaml

COMPOSE = Path(__file__).parents[1] / "docker-compose.home-server.yml"
GENERIC_COMPOSE = Path(__file__).parents[1] / "docker-compose.yml"
DOCKERFILE = Path(__file__).parents[1] / "Dockerfile"


def test_home_server_compose_has_read_only_vault_and_nested_write_overlays():
    document = yaml.safe_load(COMPOSE.read_text())
    service = document["services"]["obsidian-mcp"]

    assert "ports" not in service
    assert "volumes" not in document
    assert service["build"]["context"] == "."
    assert service["user"] == "${PUID:-1000}:${PGID:-1000}"
    assert "${HOST_VAULT_PATH:?set HOST_VAULT_PATH in .env}:/vault:ro" in service["volumes"]
    assert any(":/vault/${AI_MEMORY_PATH:-AI-Memory}:rw" in mount for mount in service["volumes"])
    assert any(":/vault/${AI_OUTPUT_PATH:-AI-Output}:rw" in mount for mount in service["volumes"])
    assert "${MCP_DATA_PATH:?set MCP_DATA_PATH in .env}:/data:rw" in service["volumes"]
    assert service["environment"]["WRITE_PATHS"] == "${AI_MEMORY_PATH:-AI-Memory}/,${AI_OUTPUT_PATH:-AI-Output}/"
    assert service["environment"]["DENY_WRITE_PATHS"] == ".obsidian/,.trash/,_AI_INSTRUCTIONS.md"
    assert service["environment"]["EXCLUDE_PATHS"] == "private/,.obsidian/,.trash/"
    assert service["environment"]["TOOL_PROFILE"] == "focused"
    for flag in ("ENABLE_MOVE", "ENABLE_FOLDER_RENAME", "ENABLE_BULK_REPLACE"):
        assert service["environment"][flag] == "${" + flag + ":-false}"
    assert service["environment"]["ENABLE_DELETE"] == "false"
    assert service["environment"]["REQUIRE_WRITE_PRECONDITIONS"] == (
        "${REQUIRE_WRITE_PRECONDITIONS:-true}"
    )
    assert service["environment"]["INDEX_RECONCILE_INTERVAL"] == (
        "${INDEX_RECONCILE_INTERVAL:-900}"
    )


def test_home_server_compose_uses_private_tunnel_network():
    document = yaml.safe_load(COMPOSE.read_text())
    assert document["networks"]["mcp-private"]["internal"] is True
    assert document["services"]["obsidian-mcp"]["networks"] == ["mcp-private"]
    assert set(document["services"]["cloudflared"]["networks"]) == {"mcp-private", "egress"}
    assert document["services"]["cloudflared"]["image"].startswith("${CLOUDFLARED_IMAGE:?")
    assert document["services"]["cloudflared"]["environment"]["TUNNEL_TOKEN"].startswith("${")
    assert document["services"]["cloudflared"]["depends_on"] == ["obsidian-mcp"]


def test_generic_compose_defaults_to_read_only_and_persistent_fastmcp_home():
    document = yaml.safe_load(GENERIC_COMPOSE.read_text())
    environment = document["services"]["obsidian-mcp"]["environment"]
    assert "READ_ONLY=${READ_ONLY:-true}" in environment
    assert "FASTMCP_HOME=${FASTMCP_HOME:-/data/fastmcp}" in environment
    assert "REQUIRE_WRITE_PRECONDITIONS=${REQUIRE_WRITE_PRECONDITIONS:-true}" in environment
    assert "TOOL_PROFILE=${TOOL_PROFILE:-full}" in environment


def test_compose_services_inherit_image_healthcheck_with_startup_grace():
    home_service = yaml.safe_load(COMPOSE.read_text())["services"]["obsidian-mcp"]
    generic_service = yaml.safe_load(GENERIC_COMPOSE.read_text())["services"]["obsidian-mcp"]

    assert "healthcheck" not in home_service
    assert "healthcheck" not in generic_service
    dockerfile = DOCKERFILE.read_text()
    assert "HEALTHCHECK" in dockerfile
    assert "--start-period=60s" in dockerfile
    assert "http://localhost:8000/health" in dockerfile
