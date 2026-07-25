"""Load and validate environment configuration."""

from __future__ import annotations

import os
from pathlib import Path


class ConfigError(Exception):
    pass


class Config:
    vault_path: Path
    read_only: bool
    write_paths: list[str]
    exclude_paths: list[str]
    transport: str
    host: str
    port: int
    api_key: str
    public_base_url: str

    def __init__(self) -> None:
        raw_vault = os.environ.get("VAULT_PATH", "")
        if not raw_vault:
            raise ConfigError("VAULT_PATH is required")
        self.vault_path = Path(raw_vault).resolve()
        if not self.vault_path.is_dir():
            raise ConfigError(f"VAULT_PATH does not exist or is not a directory: {self.vault_path}")

        self.read_only = os.environ.get("READ_ONLY", "false").lower() in ("1", "true", "yes")

        raw_write = os.environ.get("WRITE_PATHS", "")
        self.write_paths = [p.strip() for p in raw_write.split(",") if p.strip()]

        raw_exclude = os.environ.get("EXCLUDE_PATHS", "private,.obsidian")
        self.exclude_paths = [p.strip() for p in raw_exclude.split(",") if p.strip()]

        self.transport = os.environ.get("TRANSPORT", "stdio")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8000"))
        self.api_key = os.environ.get("API_KEY") or os.environ.get("OBSIDIAN_MCP_API_KEY") or ""
        self.public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

        if self.transport != "stdio" and not self.api_key:
            raise ConfigError(
                f"API_KEY is required when TRANSPORT={self.transport} "
                "(the server would otherwise be reachable without authentication)"
            )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
