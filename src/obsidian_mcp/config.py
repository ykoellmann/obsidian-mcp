"""Load and validate environment configuration.

Two modes, chosen by whether VAULTS_CONFIG is set:

- Single-vault (default, unchanged from before multi-vault support): one
  vault from VAULT_PATH/WRITE_PATHS/EXCLUDE_PATHS, one global API_KEY/
  OAUTH_GITHUB_ALLOWED_LOGINS. No migration needed for existing deployments.
- Multi-vault: VAULTS_CONFIG points at a JSON file declaring named vaults
  and identities (API keys / GitHub logins) mapped to the vaults each may
  access. Which vault a given request resolves to is tracked via a
  contextvar, set once per tool call by VaultResolutionMiddleware in
  server.py — every other module keeps calling get_config().vault_path
  etc. unchanged, since those become properties that resolve against
  whichever vault is current.
"""

from __future__ import annotations

import contextvars
import json
import os
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass
class VaultEntry:
    name: str
    path: Path
    write_paths: list[str] = field(default_factory=list)
    exclude_paths: list[str] = field(default_factory=list)
    read_only: bool | None = None  # None = inherit the server-wide READ_ONLY
    description: str = ""


@dataclass
class Identity:
    """One entry from VAULTS_CONFIG's "identities" list: an API key or a
    GitHub login, and which vaults it may resolve to."""
    type: str  # "api_key" | "github_login"
    value: str
    vaults: list[str]
    default: str | None = None


_current_vault_var: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_vault", default=None
)


def set_current_vault(name: str | None) -> contextvars.Token:
    """Set the active vault for the current context (task/request). Returns
    a token — pass it to reset_current_vault() when done, typically in a
    finally block, so the setting doesn't leak past the call it was for."""
    return _current_vault_var.set(name)


def reset_current_vault(token: contextvars.Token) -> None:
    _current_vault_var.reset(token)


def load_vaults_file(path_str: str) -> tuple[dict[str, VaultEntry], list[Identity]]:
    """Parse a VAULTS_CONFIG JSON file into (vaults, identities). Shared by
    Config.__init__ (strict — raises ConfigError on any problem) and
    server.py's _build_auth() (best-effort — see its docstring for why)."""
    path = Path(path_str)
    if not path.is_file():
        raise ConfigError(f"VAULTS_CONFIG file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ConfigError(f"VAULTS_CONFIG is not valid JSON: {exc}") from exc

    vaults_raw = data.get("vaults") or {}
    if not vaults_raw:
        raise ConfigError("VAULTS_CONFIG must define at least one vault under 'vaults'")

    vaults: dict[str, VaultEntry] = {}
    for name, entry in vaults_raw.items():
        raw_path = entry.get("path", "")
        if not raw_path:
            raise ConfigError(f"Vault {name!r} in VAULTS_CONFIG is missing 'path'")
        vault_path = Path(raw_path).resolve()
        if not vault_path.is_dir():
            raise ConfigError(f"Vault {name!r} path does not exist or is not a directory: {vault_path}")
        vaults[name] = VaultEntry(
            name=name,
            path=vault_path,
            write_paths=list(entry.get("write_paths", [])),
            exclude_paths=list(entry.get("exclude_paths", ["private", ".obsidian"])),
            read_only=entry.get("read_only"),
            description=str(entry.get("description", "")),
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
    if not identities_raw:
        raise ConfigError("VAULTS_CONFIG must define at least one identity under 'identities'")

    identities: list[Identity] = []
    for ident in identities_raw:
        itype = ident.get("type")
        if itype not in ("api_key", "github_login"):
            raise ConfigError(f"Unknown identity type in VAULTS_CONFIG: {itype!r} (must be 'api_key' or 'github_login')")
        value = str(ident.get("value", ""))
        if not value:
            raise ConfigError("An identity entry in VAULTS_CONFIG is missing 'value'")
        if itype == "github_login":
            value = value.lower()
        ident_vaults = list(ident.get("vaults", []))
        if not ident_vaults:
            raise ConfigError(f"Identity {value!r} in VAULTS_CONFIG has no 'vaults'")
        for v in ident_vaults:
            if v not in vaults:
                raise ConfigError(f"Identity {value!r} references unknown vault {v!r}")
        default = ident.get("default")
        if default is None and len(ident_vaults) == 1:
            default = ident_vaults[0]
        if default is not None and default not in ident_vaults:
            raise ConfigError(f"Identity {value!r} default vault {default!r} is not in its own 'vaults' list")
        identities.append(Identity(type=itype, value=value, vaults=ident_vaults, default=default))

    return vaults, identities


class Config:
    # Server-wide (not per-vault) settings.
    transport: str
    host: str
    port: int
    public_base_url: str
    oauth_github_client_id: str
    oauth_github_client_secret: str
    enable_canvas: bool
    enable_excalidraw: bool
    enable_kanban: bool
    enable_bases: bool

    # Multi-vault state.
    multi_vault: bool
    vaults: dict[str, VaultEntry]
    identities: list[Identity]
    default_vault_name: str

    def __init__(self) -> None:
        vaults_config_path = os.environ.get("VAULTS_CONFIG", "")
        self.multi_vault = bool(vaults_config_path)
        self._global_read_only = os.environ.get("READ_ONLY", "false").lower() in ("1", "true", "yes")

        if self.multi_vault:
            self.vaults, self.identities = load_vaults_file(vaults_config_path)
            self.default_vault_name = next(iter(self.vaults))
            # Single global api_key/oauth_github_allowed_logins don't apply
            # in multi-vault mode — identities in VAULTS_CONFIG are the
            # single source of truth for who may authenticate at all.
            self.api_key = ""
            self.oauth_github_allowed_logins = [
                i.value for i in self.identities if i.type == "github_login"
            ]
        else:
            raw_vault = os.environ.get("VAULT_PATH", "")
            if not raw_vault:
                raise ConfigError("VAULT_PATH is required")
            vault_path = Path(raw_vault).resolve()
            if not vault_path.is_dir():
                raise ConfigError(f"VAULT_PATH does not exist or is not a directory: {vault_path}")

            raw_write = os.environ.get("WRITE_PATHS", "")
            raw_exclude = os.environ.get("EXCLUDE_PATHS", "private,.obsidian")
            self.vaults = {
                "default": VaultEntry(
                    name="default",
                    path=vault_path,
                    write_paths=[p.strip() for p in raw_write.split(",") if p.strip()],
                    exclude_paths=[p.strip() for p in raw_exclude.split(",") if p.strip()],
                )
            }
            self.identities = []
            self.default_vault_name = "default"

            self.api_key = os.environ.get("API_KEY") or os.environ.get("OBSIDIAN_MCP_API_KEY") or ""
            raw_logins = os.environ.get("OAUTH_GITHUB_ALLOWED_LOGINS", "")
            self.oauth_github_allowed_logins = [
                login.strip().lower() for login in raw_logins.split(",") if login.strip()
            ]

        # Optional plugin-format tool groups — opt-in, disabled by default.
        self.enable_canvas = os.environ.get("ENABLE_CANVAS", "false").lower() in ("1", "true", "yes")
        self.enable_excalidraw = os.environ.get("ENABLE_EXCALIDRAW", "false").lower() in ("1", "true", "yes")
        self.enable_kanban = os.environ.get("ENABLE_KANBAN", "false").lower() in ("1", "true", "yes")
        self.enable_bases = os.environ.get("ENABLE_BASES", "false").lower() in ("1", "true", "yes")

        self.transport = os.environ.get("TRANSPORT", "stdio")
        if self.multi_vault and self.transport == "stdio":
            raise ConfigError(
                "VAULTS_CONFIG requires an authenticated network transport; "
                "TRANSPORT=stdio cannot identify the calling identity"
            )
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8000"))
        self.public_base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

        self.oauth_github_client_id = os.environ.get("OAUTH_GITHUB_CLIENT_ID", "")
        self.oauth_github_client_secret = os.environ.get("OAUTH_GITHUB_CLIENT_SECRET", "")
        oauth_configured = bool(self.oauth_github_client_id or self.oauth_github_client_secret)
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

        has_api_keys = bool(self.api_key) or any(i.type == "api_key" for i in self.identities)
        if self.transport != "stdio" and not has_api_keys and not oauth_configured:
            raise ConfigError(
                f"API_KEY or GitHub OAuth (OAUTH_GITHUB_CLIENT_ID/SECRET) is required when "
                f"TRANSPORT={self.transport} (the server would otherwise be reachable without "
                "authentication) — or 'api_key'/'github_login' identities via VAULTS_CONFIG"
            )

    def resolve_vault_name(self) -> str:
        """The vault the current context (contextvar, set per tool call by
        VaultResolutionMiddleware) resolves to — the default vault outside
        any such context (e.g. custom HTTP routes, or single-vault mode
        where nothing ever sets the contextvar)."""
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
    def write_paths(self) -> list[str]:
        return self._current_vault_entry().write_paths

    @property
    def exclude_paths(self) -> list[str]:
        return self._current_vault_entry().exclude_paths

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
