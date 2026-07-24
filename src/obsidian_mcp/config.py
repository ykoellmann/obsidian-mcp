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
    auth_token: str
    transport: str

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
        self.auth_token = os.environ.get("AUTH_TOKEN", "")

        if self.transport == "streamable-http" and not self.auth_token:
            raise ConfigError("AUTH_TOKEN is required when TRANSPORT=streamable-http")


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
