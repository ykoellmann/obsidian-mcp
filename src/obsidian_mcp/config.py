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
    oauth_github_client_id: str
    oauth_github_client_secret: str
    oauth_github_allowed_logins: list[str]
    enable_canvas: bool
    enable_excalidraw: bool
    enable_kanban: bool
    enable_bases: bool

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

        # Optional plugin-format tool groups — opt-in, disabled by default.
        self.enable_canvas = os.environ.get("ENABLE_CANVAS", "false").lower() in ("1", "true", "yes")
        self.enable_excalidraw = os.environ.get("ENABLE_EXCALIDRAW", "false").lower() in ("1", "true", "yes")
        self.enable_kanban = os.environ.get("ENABLE_KANBAN", "false").lower() in ("1", "true", "yes")
        self.enable_bases = os.environ.get("ENABLE_BASES", "false").lower() in ("1", "true", "yes")

        self.transport = os.environ.get("TRANSPORT", "stdio")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8000"))
        self.api_key = os.environ.get("API_KEY") or os.environ.get("OBSIDIAN_MCP_API_KEY") or ""
        self.public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

        self.oauth_github_client_id = os.environ.get("OAUTH_GITHUB_CLIENT_ID", "")
        self.oauth_github_client_secret = os.environ.get("OAUTH_GITHUB_CLIENT_SECRET", "")
        raw_logins = os.environ.get("OAUTH_GITHUB_ALLOWED_LOGINS", "")
        self.oauth_github_allowed_logins = [
            login.strip().lower() for login in raw_logins.split(",") if login.strip()
        ]
        oauth_configured = bool(self.oauth_github_client_id or self.oauth_github_client_secret)
        if oauth_configured:
            if not (self.oauth_github_client_id and self.oauth_github_client_secret):
                raise ConfigError(
                    "OAUTH_GITHUB_CLIENT_ID and OAUTH_GITHUB_CLIENT_SECRET must both be set "
                    "to enable GitHub OAuth"
                )
            if not self.oauth_github_allowed_logins:
                raise ConfigError(
                    "OAUTH_GITHUB_ALLOWED_LOGINS is required when GitHub OAuth is configured "
                    "(comma-separated GitHub usernames) — without it, any GitHub account could "
                    "authenticate and get full access to the vault"
                )
            if not self.public_base_url:
                raise ConfigError(
                    "PUBLIC_BASE_URL is required when GitHub OAuth is configured "
                    "(used as the OAuth callback base URL, e.g. https://your-server.com)"
                )

        if self.transport != "stdio" and not self.api_key and not oauth_configured:
            raise ConfigError(
                f"API_KEY or GitHub OAuth (OAUTH_GITHUB_CLIENT_ID/SECRET) is required when "
                f"TRANSPORT={self.transport} "
                "(the server would otherwise be reachable without authentication)"
            )


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
