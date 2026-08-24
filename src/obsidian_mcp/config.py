"""Load and validate single- or multi-vault environment configuration.

When ``VAULTS_CONFIG`` is set, the active vault is selected per request by
``VaultResolutionMiddleware``. Filesystem policy remains per-vault: every
property used by storage resolves through the same context variable as the
vault root.
"""

from __future__ import annotations

import contextvars
import json
import math
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from .storage.policy import VaultPathError, normalise_path_rules, path_rules_from_env


class ConfigError(Exception):
    pass


def get_tool_profile() -> str:
    """Read and validate TOOL_PROFILE without requiring vault settings."""
    value = os.environ.get("TOOL_PROFILE", "full").strip().lower()
    if value not in ("full", "focused"):
        raise ConfigError(
            f"Invalid TOOL_PROFILE={value!r}; expected 'full' or 'focused'"
        )
    return value


def _path_rules(value: object, *, name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Normalize JSON path rules without comma-delimited env parsing."""
    if value is None:
        values = list(default)
    elif isinstance(value, (list, tuple)) and all(isinstance(item, str) for item in value):
        values = list(value)
    else:
        raise ConfigError(f"{name} must be a list of strings")
    try:
        return normalise_path_rules(values, name=name)
    except VaultPathError as exc:
        raise ConfigError(str(exc)) from exc


@dataclass(frozen=True)
class VaultEntry:
    name: str
    path: Path
    write_paths: tuple[str, ...] = ()
    read_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    deny_read_paths: tuple[str, ...] = (".obsidian/", ".trash/")
    deny_write_paths: tuple[str, ...] = (
        ".obsidian/",
        ".trash/",
        "_AI_INSTRUCTIONS.md",
    )
    read_only: bool | None = None
    description: str = ""


@dataclass(frozen=True)
class Identity:
    """One API key or GitHub login and the vaults it may access."""

    type: str
    value: str
    vaults: tuple[str, ...]
    default: str | None = None


_current_vault_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_vault", default=None
)


def set_current_vault(name: str | None) -> contextvars.Token:
    return _current_vault_var.set(name)


def reset_current_vault(token: contextvars.Token) -> None:
    _current_vault_var.reset(token)


def load_vaults_file(path_str: str) -> tuple[dict[str, VaultEntry], tuple[Identity, ...]]:
    """Parse and validate ``VAULTS_CONFIG`` JSON."""
    path = Path(path_str)
    if not path.is_file():
        raise ConfigError(f"VAULTS_CONFIG file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"VAULTS_CONFIG is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError("VAULTS_CONFIG must contain a JSON object")

    vaults_raw = data.get("vaults") or {}
    if not isinstance(vaults_raw, dict) or not vaults_raw:
        raise ConfigError("VAULTS_CONFIG must define at least one vault under 'vaults'")

    vaults: dict[str, VaultEntry] = {}
    for name, raw_entry in vaults_raw.items():
        if not isinstance(name, str) or not name:
            raise ConfigError("Vault names in VAULTS_CONFIG must be non-empty strings")
        if not isinstance(raw_entry, dict):
            raise ConfigError(f"Vault {name!r} in VAULTS_CONFIG must be an object")
        raw_path = raw_entry.get("path", "")
        if not isinstance(raw_path, str) or not raw_path:
            raise ConfigError(f"Vault {name!r} in VAULTS_CONFIG is missing 'path'")
        vault_path = Path(raw_path).resolve()
        if not vault_path.is_dir():
            raise ConfigError(
                f"Vault {name!r} path does not exist or is not a directory: {vault_path}"
            )
        read_only = raw_entry.get("read_only")
        if read_only is not None and not isinstance(read_only, bool):
            raise ConfigError(f"Vault {name!r} read_only must be a boolean")
        vaults[name] = VaultEntry(
            name=name,
            path=vault_path,
            write_paths=_path_rules(
                raw_entry.get("write_paths"), name=f"vault {name!r} write_paths"
            ),
            read_paths=_path_rules(
                raw_entry.get("read_paths"), name=f"vault {name!r} read_paths"
            ),
            exclude_paths=_path_rules(
                raw_entry.get("exclude_paths"),
                name=f"vault {name!r} exclude_paths",
                default=("private/", ".obsidian/", ".trash/"),
            ),
            deny_read_paths=_path_rules(
                raw_entry.get("deny_read_paths"),
                name=f"vault {name!r} deny_read_paths",
                default=(".obsidian/", ".trash/"),
            ),
            deny_write_paths=_path_rules(
                raw_entry.get("deny_write_paths"),
                name=f"vault {name!r} deny_write_paths",
                default=(".obsidian/", ".trash/", "_AI_INSTRUCTIONS.md"),
            ),
            read_only=read_only,
            description=str(raw_entry.get("description", "")),
        )

    vault_items = list(vaults.items())
    for index, (name, vault) in enumerate(vault_items):
        for other_name, other_vault in vault_items[index + 1:]:
            if (
                vault.path == other_vault.path
                or vault.path in other_vault.path.parents
                or other_vault.path in vault.path.parents
            ):
                raise ConfigError(
                    f"Vault paths must not overlap: {name!r} ({vault.path}) and "
                    f"{other_name!r} ({other_vault.path})"
                )

    identities_raw = data.get("identities") or []
    if not isinstance(identities_raw, list) or not identities_raw:
        raise ConfigError("VAULTS_CONFIG must define at least one identity under 'identities'")

    identities: list[Identity] = []
    for raw_identity in identities_raw:
        if not isinstance(raw_identity, dict):
            raise ConfigError("Identity entries in VAULTS_CONFIG must be objects")
        identity_type = raw_identity.get("type")
        if identity_type not in ("api_key", "github_login"):
            raise ConfigError(
                f"Unknown identity type in VAULTS_CONFIG: {identity_type!r} "
                "(must be 'api_key' or 'github_login')"
            )
        value = str(raw_identity.get("value", ""))
        if not value:
            raise ConfigError("An identity entry in VAULTS_CONFIG is missing 'value'")
        if identity_type == "github_login":
            value = value.lower()
        raw_identity_vaults = raw_identity.get("vaults", [])
        if not isinstance(raw_identity_vaults, list) or not all(
            isinstance(item, str) for item in raw_identity_vaults
        ):
            raise ConfigError(f"Identity {value!r} 'vaults' must be a list of strings")
        identity_vaults = tuple(raw_identity_vaults)
        if not identity_vaults:
            raise ConfigError(f"Identity {value!r} in VAULTS_CONFIG has no 'vaults'")
        for vault_name in identity_vaults:
            if vault_name not in vaults:
                raise ConfigError(
                    f"Identity {value!r} references unknown vault {vault_name!r}"
                )
        default = raw_identity.get("default")
        if default is None and len(identity_vaults) == 1:
            default = identity_vaults[0]
        if default is not None and default not in identity_vaults:
            raise ConfigError(
                f"Identity {value!r} default vault {default!r} is not in its own 'vaults' list"
            )
        identities.append(
            Identity(
                type=identity_type,
                value=value,
                vaults=identity_vaults,
                default=default,
            )
        )
    return vaults, tuple(identities)


@dataclass(frozen=True, init=False)
class Config:
    """Validated immutable configuration with context-selected vault fields."""

    multi_vault: bool = field(init=False)
    vaults: Mapping[str, VaultEntry] = field(init=False)
    identities: tuple[Identity, ...] = field(init=False)
    default_vault_name: str = field(init=False)
    _global_read_only: bool = field(init=False, repr=False)
    lock_path: Path = field(init=False)
    audit_log_path: Path = field(init=False)
    allow_permanent_delete: bool = field(init=False)
    max_attachment_bytes: int = field(init=False)
    require_write_preconditions: bool = field(init=False)
    index_reconcile_interval: float = field(init=False)
    watcher_debounce_ms: int = field(init=False)
    transport: str = field(init=False)
    host: str = field(init=False)
    port: int = field(init=False)
    api_key: str = field(init=False, repr=False)
    public_base_url: str = field(init=False)
    oauth_github_client_id: str = field(init=False)
    oauth_github_client_secret: str = field(init=False, repr=False)
    oauth_github_allowed_logins: tuple[str, ...] = field(init=False)
    enable_canvas: bool = field(init=False)
    enable_excalidraw: bool = field(init=False)
    enable_kanban: bool = field(init=False)
    enable_bases: bool = field(init=False)
    enable_move: bool = field(init=False)
    enable_folder_rename: bool = field(init=False)
    enable_bulk_replace: bool = field(init=False)
    enable_delete: bool = field(init=False)
    tool_profile: str = field(init=False)

    def __init__(self) -> None:
        set_value = object.__setattr__
        set_value(self, "tool_profile", get_tool_profile())
        vaults_config_path = os.environ.get("VAULTS_CONFIG", "")
        set_value(self, "multi_vault", bool(vaults_config_path))
        set_value(
            self,
            "_global_read_only",
            os.environ.get("READ_ONLY", "false").lower() in ("1", "true", "yes"),
        )

        if self.multi_vault:
            vaults, identities = load_vaults_file(vaults_config_path)
            set_value(self, "vaults", MappingProxyType(vaults))
            set_value(self, "identities", identities)
            set_value(self, "default_vault_name", next(iter(vaults)))
            set_value(self, "api_key", "")
            set_value(
                self,
                "oauth_github_allowed_logins",
                tuple(identity.value for identity in identities if identity.type == "github_login"),
            )
        else:
            raw_vault = os.environ.get("VAULT_PATH", "")
            if not raw_vault:
                raise ConfigError("VAULT_PATH is required")
            vault_path = Path(raw_vault).resolve()
            if not vault_path.is_dir():
                raise ConfigError(
                    f"VAULT_PATH does not exist or is not a directory: {vault_path}"
                )
            try:
                entry = VaultEntry(
                    name="default",
                    path=vault_path,
                    write_paths=tuple(
                        path_rules_from_env(
                            os.environ.get("WRITE_PATHS", ""), name="WRITE_PATHS"
                        )
                    ),
                    read_paths=tuple(
                        path_rules_from_env(
                            os.environ.get("READ_PATHS", ""), name="READ_PATHS"
                        )
                    ),
                    exclude_paths=tuple(
                        path_rules_from_env(
                            os.environ.get("EXCLUDE_PATHS", "private/,.obsidian/,.trash/"),
                            name="EXCLUDE_PATHS",
                        )
                    ),
                    deny_read_paths=tuple(
                        path_rules_from_env(
                            os.environ.get("DENY_READ_PATHS", ".obsidian/,.trash/"),
                            name="DENY_READ_PATHS",
                        )
                    ),
                    deny_write_paths=tuple(
                        path_rules_from_env(
                            os.environ.get(
                                "DENY_WRITE_PATHS",
                                ".obsidian/,.trash/,_AI_INSTRUCTIONS.md",
                            ),
                            name="DENY_WRITE_PATHS",
                        )
                    ),
                )
            except VaultPathError as exc:
                raise ConfigError(str(exc)) from exc
            set_value(self, "vaults", MappingProxyType({"default": entry}))
            set_value(self, "identities", ())
            set_value(self, "default_vault_name", "default")
            set_value(
                self,
                "api_key",
                os.environ.get("API_KEY") or os.environ.get("OBSIDIAN_MCP_API_KEY") or "",
            )
            raw_logins = os.environ.get("OAUTH_GITHUB_ALLOWED_LOGINS", "")
            set_value(
                self,
                "oauth_github_allowed_logins",
                tuple(login.strip().lower() for login in raw_logins.split(",") if login.strip()),
            )

        raw_lock_path = os.environ.get("LOCK_PATH", "")
        if raw_lock_path:
            lock_path = Path(raw_lock_path).expanduser().resolve()
        elif os.environ.get("FASTMCP_HOME"):
            lock_path = (Path(os.environ["FASTMCP_HOME"]).expanduser() / "locks").resolve()
        else:
            lock_path = (Path(tempfile.gettempdir()) / "obsidian-mcp-locks").resolve()
        for vault in self.vaults.values():
            if lock_path == vault.path or vault.path in lock_path.parents:
                raise ConfigError("LOCK_PATH must be outside every configured vault")
        set_value(self, "lock_path", lock_path)

        raw_audit_path = os.environ.get("AUDIT_LOG_PATH", "")
        audit_log_path = (
            Path(os.path.abspath(Path(raw_audit_path).expanduser()))
            if raw_audit_path
            else lock_path / "audit.jsonl"
        )
        resolved_audit_parent = audit_log_path.parent.resolve()
        for vault in self.vaults.values():
            if (
                audit_log_path == vault.path
                or vault.path in audit_log_path.parents
                or resolved_audit_parent == vault.path
                or vault.path in resolved_audit_parent.parents
            ):
                raise ConfigError("AUDIT_LOG_PATH must be outside every configured vault")
        set_value(self, "audit_log_path", audit_log_path)

        set_value(
            self,
            "allow_permanent_delete",
            os.environ.get("ALLOW_PERMANENT_DELETE", "false").lower()
            in ("1", "true", "yes"),
        )
        raw_max_attachment_bytes = os.environ.get(
            "MAX_ATTACHMENT_BYTES", str(25 * 1024 * 1024)
        )
        try:
            max_attachment_bytes = int(raw_max_attachment_bytes)
        except ValueError as exc:
            raise ConfigError("MAX_ATTACHMENT_BYTES must be a positive integer") from exc
        if max_attachment_bytes <= 0:
            raise ConfigError("MAX_ATTACHMENT_BYTES must be a positive integer")
        set_value(self, "max_attachment_bytes", max_attachment_bytes)

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
        if self.multi_vault and self.transport == "stdio":
            raise ConfigError(
                "VAULTS_CONFIG requires an authenticated network transport; "
                "TRANSPORT=stdio cannot identify the calling identity"
            )
        set_value(self, "host", os.environ.get("HOST", "0.0.0.0"))
        set_value(self, "port", int(os.environ.get("PORT", "8000")))
        set_value(self, "public_base_url", os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"))
        set_value(
            self, "oauth_github_client_id", os.environ.get("OAUTH_GITHUB_CLIENT_ID", "")
        )
        set_value(
            self,
            "oauth_github_client_secret",
            os.environ.get("OAUTH_GITHUB_CLIENT_SECRET", ""),
        )

        oauth_configured = bool(
            self.oauth_github_client_id or self.oauth_github_client_secret
        )
        if oauth_configured:
            if not (self.oauth_github_client_id and self.oauth_github_client_secret):
                raise ConfigError(
                    "OAUTH_GITHUB_CLIENT_ID and OAUTH_GITHUB_CLIENT_SECRET must both be set "
                    "to enable GitHub OAuth"
                )
            if not self.oauth_github_allowed_logins:
                raise ConfigError(
                    "No allowed GitHub logins configured (OAUTH_GITHUB_ALLOWED_LOGINS, or "
                    "at least one 'github_login' identity in VAULTS_CONFIG) — without one, "
                    "any GitHub account could authenticate and get full access to a vault"
                )
            if not self.public_base_url:
                raise ConfigError(
                    "PUBLIC_BASE_URL is required when GitHub OAuth is configured "
                    "(used as the OAuth callback base URL, e.g. https://your-server.com)"
                )

        has_api_keys = bool(self.api_key) or any(
            identity.type == "api_key" for identity in self.identities
        )
        if self.transport != "stdio" and not has_api_keys and not oauth_configured:
            raise ConfigError(
                f"API_KEY or GitHub OAuth (OAUTH_GITHUB_CLIENT_ID/SECRET) is required when "
                f"TRANSPORT={self.transport} (the server would otherwise be reachable without "
                "authentication) — or 'api_key'/'github_login' identities via VAULTS_CONFIG"
            )

    def resolve_vault_name(self) -> str:
        name = _current_vault_var.get()
        if name is None:
            return self.default_vault_name
        if name not in self.vaults:
            raise ConfigError(f"Unknown vault: {name!r}")
        return name

    def _current_vault_entry(self) -> VaultEntry:
        return self.vaults[self.resolve_vault_name()]

    @property
    def vault_path(self) -> Path:
        return self._current_vault_entry().path

    @property
    def write_paths(self) -> tuple[str, ...]:
        return self._current_vault_entry().write_paths

    @property
    def read_paths(self) -> tuple[str, ...]:
        return self._current_vault_entry().read_paths

    @property
    def exclude_paths(self) -> tuple[str, ...]:
        return self._current_vault_entry().exclude_paths

    @property
    def deny_read_paths(self) -> tuple[str, ...]:
        return self._current_vault_entry().deny_read_paths

    @property
    def deny_write_paths(self) -> tuple[str, ...]:
        return self._current_vault_entry().deny_write_paths

    @property
    def read_only(self) -> bool:
        vault_read_only = self._current_vault_entry().read_only
        return self._global_read_only if vault_read_only is None else vault_read_only


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config
