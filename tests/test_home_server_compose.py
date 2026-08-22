from pathlib import Path

import yaml

COMPOSE = Path(__file__).parents[1] / "docker-compose.home-server.yml"


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
    assert service["environment"]["WRITE_PATHS"] == "${AI_MEMORY_PATH:-AI-Memory},${AI_OUTPUT_PATH:-AI-Output}"
    assert service["environment"]["DENY_WRITE_PATHS"] == ".obsidian/,.trash/,_AI_INSTRUCTIONS.md"
    for flag in ("ENABLE_MOVE", "ENABLE_FOLDER_RENAME", "ENABLE_BULK_REPLACE", "ENABLE_DELETE"):
        assert service["environment"][flag] == "${" + flag + ":-false}"


def test_home_server_compose_uses_private_tunnel_network():
    document = yaml.safe_load(COMPOSE.read_text())
    assert document["networks"]["mcp-private"]["internal"] is True
    assert document["services"]["obsidian-mcp"]["networks"] == ["mcp-private"]
    assert set(document["services"]["cloudflared"]["networks"]) == {"mcp-private", "egress"}
    assert document["services"]["cloudflared"]["image"].startswith("${CLOUDFLARED_IMAGE:?")
    assert document["services"]["cloudflared"]["environment"]["TUNNEL_TOKEN"].startswith("${")
    assert document["services"]["cloudflared"]["depends_on"] == ["obsidian-mcp"]
