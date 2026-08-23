"""Load and validate environment configuration."""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .storage.policy import VaultPathError, path_rules_from_env


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Config:
    """Validated, immutable configuration loaded from the environment."""

    vault_path: Path = field(init=False)
    read_only: bool = field(init=False)
    write_paths: tuple[str, ...] = field(init=False)
    exclude_paths: tuple[str, ...] = field(init=False)
    deny_read_paths: tuple[str, ...] = field(init=False)
    deny_write_paths: tuple[str, ...] = field(init=False)
    lock_path: Path = field(init=False)
    allow_permanent_delete: bool = field(init=False)
    max_attachment_bytes: int = field(init=False)
    require_write_preconditions: bool = field(init=False)
    index_reconcile_interval: float = field(init=False)
    watcher_debounce_ms: int = field(init=False)
    transport: str = field(init=False)
    host: str = field(init=False)
    port: int = field(init=False)
    api_key: str = field(init=False)
    public_base_url: str = field(init=False)
    oauth_github_client_id: str = field(init=False)
    oauth_github_client_secret: str = field(init=False)
    oauth_github_allowed_logins: tuple[str, ...] = field(init=False)
    enable_canvas: bool = field(init=False)
    enable_excalidraw: bool = field(init=False)
    enable_kanban: bool = field(init=False)
    enable_bases: bool = field(init=False)
    enable_move: bool = field(init=False)
    enable_folder_rename: bool = field(init=False)
    enable_bulk_replace: bool = field(init=False)
    enable_delete: bool = field(init=False)

    def __post_init__(self) -> None:
        set_value = object.__setattr__
        raw_vault = os.environ.get("VAULT_PATH", "")
        if not raw_vault:
            raise ConfigError("VAULT_PATH is required")
        set_value(self, "vault_path", Path(raw_vault).resolve())
        if not self.vault_path.is_dir():
            raise ConfigError(f"VAULT_PATH does not exist or is not a directory: {self.vault_path}")

        set_value(
            self,
            "read_only",
            os.environ.get("READ_ONLY", "false").lower() in ("1", "true", "yes"),
        )

        raw_write = os.environ.get("WRITE_PATHS", "")
        try:
            set_value(
                self, "write_paths", tuple(path_rules_from_env(raw_write, name="WRITE_PATHS"))
            )
            set_value(
                self,
                "deny_read_paths",
                tuple(
                    path_rules_from_env(
                        os.environ.get("DENY_READ_PATHS", ".obsidian/,.trash/"),
                        name="DENY_READ_PATHS",
                    )
                ),
            )
            set_value(
                self,
                "deny_write_paths",
                tuple(
                    path_rules_from_env(
                        os.environ.get(
                            "DENY_WRITE_PATHS", ".obsidian/,.trash/,_AI_INSTRUCTIONS.md"
                        ),
                        name="DENY_WRITE_PATHS",
                    )
                ),
            )
            # EXCLUDE_PATHS remains a discovery/index filter, but normalize it
            # as well so component-aware matching is consistent everywhere.
            set_value(
                self,
                "exclude_paths",
                tuple(
                    path_rules_from_env(
                        os.environ.get("EXCLUDE_PATHS", "private/,.obsidian/,.trash/"),
                        name="EXCLUDE_PATHS",
                    )
                ),
            )
        except VaultPathError as exc:
            raise ConfigError(str(exc)) from exc

        raw_lock_path = os.environ.get("LOCK_PATH", "")
        if raw_lock_path:
            set_value(self, "lock_path", Path(raw_lock_path).expanduser().resolve())
        elif os.environ.get("FASTMCP_HOME"):
            set_value(
                self,
                "lock_path",
                (Path(os.environ["FASTMCP_HOME"]).expanduser() / "locks").resolve(),
            )
        else:
            # Native installs need a usable lock domain without requiring a
            # root-owned /data directory. Docker supplies /data/locks
            # explicitly in its Compose configuration.
            set_value(
                self, "lock_path", (Path(tempfile.gettempdir()) / "obsidian-mcp-locks").resolve()
            )
        if self.lock_path == self.vault_path or self.vault_path in self.lock_path.parents:
            raise ConfigError("LOCK_PATH must be outside VAULT_PATH")

        set_value(
            self,
            "allow_permanent_delete",
            os.environ.get("ALLOW_PERMANENT_DELETE", "false").lower() in ("1", "true", "yes"),
        )
        raw_max_attachment_bytes = os.environ.get("MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024))
        try:
            set_value(self, "max_attachment_bytes", int(raw_max_attachment_bytes))
        except ValueError as exc:
            raise ConfigError("MAX_ATTACHMENT_BYTES must be a positive integer") from exc
        if self.max_attachment_bytes <= 0:
            raise ConfigError("MAX_ATTACHMENT_BYTES must be a positive integer")

        set_value(
            self,
            "require_write_preconditions",
            os.environ.get("REQUIRE_WRITE_PRECONDITIONS", "false").lower()
            in ("1", "true", "yes"),
        )
        try:
            reconcile_interval = float(os.environ.get("INDEX_RECONCILE_INTERVAL", "900"))
        except ValueError as exc:
            raise ConfigError("INDEX_RECONCILE_INTERVAL must be a finite positive number") from exc
        if not math.isfinite(reconcile_interval) or reconcile_interval <= 0:
            raise ConfigError("INDEX_RECONCILE_INTERVAL must be a finite positive number")
        set_value(self, "index_reconcile_interval", reconcile_interval)

        try:
            debounce_ms = int(os.environ.get("WATCHER_DEBOUNCE_MS", "100"))
        except ValueError as exc:
            raise ConfigError("WATCHER_DEBOUNCE_MS must be a non-negative integer") from exc
        if debounce_ms < 0:
            raise ConfigError("WATCHER_DEBOUNCE_MS must be a non-negative integer")
        set_value(self, "watcher_debounce_ms", debounce_ms)

        # Optional plugin-format tool groups — opt-in, disabled by default.
        for attribute, environment in (
            ("enable_canvas", "ENABLE_CANVAS"),
            ("enable_excalidraw", "ENABLE_EXCALIDRAW"),
            ("enable_kanban", "ENABLE_KANBAN"),
            ("enable_bases", "ENABLE_BASES"),
            ("enable_move", "ENABLE_MOVE"),
            ("enable_folder_rename", "ENABLE_FOLDER_RENAME"),
            ("enable_bulk_replace", "ENABLE_BULK_REPLACE"),
            ("enable_delete", "ENABLE_DELETE"),
        ):
            set_value(
                self,
                attribute,
                os.environ.get(environment, "false").lower() in ("1", "true", "yes"),
            )

        set_value(self, "transport", os.environ.get("TRANSPORT", "stdio"))
        set_value(self, "host", os.environ.get("HOST", "0.0.0.0"))
        set_value(self, "port", int(os.environ.get("PORT", "8000")))
        set_value(
            self,
            "api_key",
            os.environ.get("API_KEY") or os.environ.get("OBSIDIAN_MCP_API_KEY") or "",
        )
        set_value(self, "public_base_url", os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"))

        set_value(self, "oauth_github_client_id", os.environ.get("OAUTH_GITHUB_CLIENT_ID", ""))
        set_value(
            self, "oauth_github_client_secret", os.environ.get("OAUTH_GITHUB_CLIENT_SECRET", "")
        )
        raw_logins = os.environ.get("OAUTH_GITHUB_ALLOWED_LOGINS", "")
        set_value(
            self,
            "oauth_github_allowed_logins",
            tuple(login.strip().lower() for login in raw_logins.split(",") if login.strip()),
        )
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
