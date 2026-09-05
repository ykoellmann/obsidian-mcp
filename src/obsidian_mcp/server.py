"""FastMCP server entry point."""

from __future__ import annotations

import hmac
import logging
import mimetypes
import os
import threading
from dataclasses import dataclass
from functools import wraps

from fastmcp import FastMCP
from fastmcp.server.auth import AccessToken, AuthProvider, MultiAuth, TokenVerifier
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import canonical_server
from .config import (
    ConfigError,
    get_config,
    load_vaults_file,
    reset_current_vault,
    set_current_vault,
)
from .domain.index import VaultIndex
from .domain.models import PreconditionRequiredError, RevisionConflictError
from .storage.filesystem import VaultStorage
from .storage.policy import (
    InvalidFileTypeError,
    ReadPermissionError,
    VaultAccessPolicy,
    VaultPathError,
    WritePermissionError,
)
from .storage.watcher import VaultWatcher
from .tools.attachments import (
    AttachmentTooLargeError,
    validate_attachment_path,
    verify_attachment_token,
    write_attachment_bytes,
)
from .tools.bases import list_bases, patch_base, read_base, write_base
from .tools.canvas import list_canvases, patch_canvas, read_canvas, write_canvas
from .tools.excalidraw import (
    list_excalidraw,
    patch_excalidraw,
    read_excalidraw,
    write_excalidraw,
)
from .tools.kanban import (
    add_kanban_card,
    create_kanban_board,
    delete_kanban_card,
    move_kanban_card,
    read_kanban,
)
from .tools.prompts import daily_note_prompt, weekly_review_prompt

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)

def _mutation_boundary(function):
    """Return expected optimistic-concurrency failures as MCP error results."""

    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (RevisionConflictError, PreconditionRequiredError) as exc:
            return ToolResult(structured_content=exc.to_dict(), is_error=True)

    return wrapped


_INSTRUCTIONS = canonical_server.INSTRUCTIONS


def _load_instructions() -> str:
    return _INSTRUCTIONS


class _APIKeyAuthProvider(TokenVerifier):
    """Static API-key auth, one or more keys. Clients must send:
    Authorization: Bearer <key>. client_id on the returned AccessToken is
    the matched key itself — in multi-vault mode that's what
    VaultResolutionMiddleware looks up in the identities table (single-key
    mode has no such lookup, so the exact client_id value doesn't matter
    there beyond being stable)."""

    def __init__(self, api_keys: list[str]) -> None:
        super().__init__()
        self._keys = api_keys

    async def verify_token(self, token: str) -> AccessToken | None:
        for key in self._keys:
            if hmac.compare_digest(token, key):
                return AccessToken(token=token, client_id=key, scopes=[])
        logger.warning("Rejected request with invalid API key")
        return None


class _RestrictedGitHubVerifier(TokenVerifier):
    """Wraps GitHubProvider's token validator to reject logins not on an allowlist.

    Runs once per GitHub token exchange (not per request) — GitHubProvider
    itself has no concept of restricting which GitHub account may authenticate,
    so any account could otherwise get full vault access.
    """

    def __init__(self, base: TokenVerifier, allowed_logins: list[str]) -> None:
        super().__init__()
        self._base = base
        self._allowed_logins = set(allowed_logins)

    async def verify_token(self, token: str) -> AccessToken | None:
        result = await self._base.verify_token(token)
        if result is None:
            return None
        login = str((result.claims or {}).get("login", "")).lower()
        if login not in self._allowed_logins:
            logger.warning("Rejected GitHub login not on allowlist: %s", login or "<unknown>")
            return None
        return result


def _identities_from_env() -> tuple[list[str], list[str]]:
    """(api_keys, allowed_github_logins) — from VAULTS_CONFIG if set, else
    the legacy single-vault env vars. Reads os.environ directly (not
    get_config()) so this module can still be imported without VAULT_PATH
    set (e.g. during testing or linting); a broken VAULTS_CONFIG here is
    swallowed (falls back to empty — no auth configured) rather than
    raised, since Config.__init__ is the actual place that validates it and
    fails loudly at real startup. Don't let a parse error here silently
    grant unauthenticated access, though — an empty result just means no
    verifier gets built at all, so the server refuses to start over the
    network per the has_api_keys/oauth_configured check in Config.__init__."""
    vaults_config_path = os.environ.get("VAULTS_CONFIG", "")
    if vaults_config_path:
        try:
            _vaults, identities = load_vaults_file(vaults_config_path)
        except ConfigError:
            return [], []
        api_keys = [i.value for i in identities if i.type == "api_key"]
        allowed_logins = [i.value for i in identities if i.type == "github_login"]
        return api_keys, allowed_logins

    key = os.environ.get("API_KEY") or os.environ.get("OBSIDIAN_MCP_API_KEY")
    api_keys = [key] if key else []
    allowed_logins = [
        login.strip().lower()
        for login in os.environ.get("OAUTH_GITHUB_ALLOWED_LOGINS", "").split(",")
        if login.strip()
    ]
    return api_keys, allowed_logins


def _build_auth() -> AuthProvider | None:
    api_keys, allowed_logins = _identities_from_env()
    api_key_verifier = _APIKeyAuthProvider(api_keys) if api_keys else None

    client_id = os.environ.get("OAUTH_GITHUB_CLIENT_ID")
    client_secret = os.environ.get("OAUTH_GITHUB_CLIENT_SECRET")
    github_provider = None
    if client_id and client_secret:
        base_url = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")

        # Client registrations + encrypted tokens persist under FastMCP's own
        # data directory (FASTMCP_HOME, defaults to a platformdirs path).
        # Mount that directory as a volume in Docker, or set FASTMCP_HOME to a
        # path inside an existing mount, or logins won't survive a restart.
        github_provider = GitHubProvider(
            client_id=client_id,
            client_secret=client_secret,
            base_url=base_url,
            # Otherwise every MCP request waits on GitHub /user and /user/repos.
            cache_ttl_seconds=300,
        )
        # Relies on OAuthProxy's private _token_validator attribute — the only
        # hook that runs at token-exchange time, before an allowlist check
        # could otherwise happen. May break on a fastmcp upgrade; if it does,
        # this will raise AttributeError loudly at startup rather than
        # silently allowing any GitHub account through.
        github_provider._token_validator = _RestrictedGitHubVerifier(
            github_provider._token_validator, allowed_logins
        )
        logger.info("GitHub OAuth enabled (allowed logins: %s)", ", ".join(allowed_logins))

    if github_provider and api_key_verifier:
        logger.info("API key auth enabled (alongside GitHub OAuth)")
        # required_scopes must be cleared here: MultiAuth defaults it to the
        # server's (GitHubProvider's, i.e. ["user"]) and enforces it across
        # every verifier. _APIKeyAuthProvider's tokens carry scopes=[] since
        # a static key has no OAuth scopes, so without this override every
        # API-key request would fail with "insufficient_scope" even though
        # the key itself checked out.
        return MultiAuth(server=github_provider, verifiers=[api_key_verifier], required_scopes=[])
    if github_provider:
        return github_provider
    if api_key_verifier:
        logger.info("API key auth enabled")
        return api_key_verifier
    return None


class _CurrentVaultIndex:
    """Every existing @mcp.tool()/@mcp.resource() call site passes/uses
    `_index` as a single VaultIndex — this proxy lets that keep working
    completely unchanged even though there's now one VaultIndex per
    configured vault. Attribute access is forwarded to whichever vault's
    index the current context resolves to (contextvars, set per tool call
    by VaultResolutionMiddleware in multi-vault mode; the single configured
    vault otherwise)."""

    def __getattr__(self, name: str):
        vault_name = get_config().resolve_vault_name()
        return getattr(_indices[vault_name], name)


def _identity_for_token(cfg, access_token: AccessToken | None) -> object:
    """Find the Identity (config.Identity) matching a given AccessToken —
    GitHub login if the token carries one (OAuth), else the API key itself
    (client_id, see _APIKeyAuthProvider). Raises PermissionError if there's
    no token or no VAULTS_CONFIG entry for it — never silently falls back
    to some default vault for an identity nothing was configured for.

    Split out from _resolve_identity() (which reads the token from FastMCP's
    request context via get_access_token()) so callers that already have an
    AccessToken in hand from doing their own manual verification — the
    /attachments/* custom route, which bypasses FastMCP's normal auth
    middleware entirely — can resolve an identity too, without needing
    get_access_token() to work in a context it doesn't cover.
    """
    if access_token is None:
        raise PermissionError("No authenticated identity available to resolve a vault for")

    login = str((access_token.claims or {}).get("login", "")).lower()
    candidates = [("github_login", login)] if login else []
    candidates.append(("api_key", access_token.client_id))

    for itype, value in candidates:
        if not value:
            continue
        for identity in cfg.identities:
            if identity.type == itype and identity.value == value:
                return identity

    raise PermissionError("This identity has no vault access configured in VAULTS_CONFIG")


def _resolve_identity(cfg) -> object:
    """_identity_for_token() using the current request's AccessToken from
    FastMCP's own context (get_access_token()) — the normal case for
    everything that goes through MCP tool-call/resource-read dispatch."""
    return _identity_for_token(cfg, get_access_token())


def _select_vault(identity, requested: str | None) -> str:
    """Which vault an identity's call actually resolves to.

    requested=None (no vault= argument given): the identity's configured
    "default" — every identity has one automatically if it only has a
    single allowed vault (see load_vaults_file); an identity with several
    allowed vaults and no explicit "default" in VAULTS_CONFIG must pass
    vault= explicitly, every single call, rather than have the server
    silently guess which one it means.
    requested=<name>: must be one of the identity's allowed vaults.
    """
    if requested is None:
        if identity.default is None:
            raise PermissionError(
                f"This identity has access to multiple vaults ({identity.vaults}) and no "
                "default is configured — pass vault=<name> explicitly on this call"
            )
        return identity.default
    if requested not in identity.vaults:
        raise PermissionError(f"This identity does not have access to vault {requested!r}")
    return requested


class VaultResolutionMiddleware(Middleware):
    """In multi-vault mode, resolves which vault a tool call/resource read
    operates on and sets the current-vault contextvar for its duration —
    every tools/*.py function then transparently sees the right
    get_config().vault_path/write_paths/exclude_paths/read_only and the
    right VaultIndex (via _CurrentVaultIndex above), without any of them
    needing to know multi-vault exists. A no-op pass-through in
    single-vault mode (VAULTS_CONFIG unset).

    Every @mcp.tool() has an optional `vault: str | None = None` parameter
    (mechanical addition, not used by the tool bodies themselves — this
    middleware is the only thing that reads it, from the raw call
    arguments, before the tool function ever runs). Omit it to use the
    calling identity's default vault; pass it to operate on a different one
    of that identity's allowed vaults for just this one call. See
    list_vaults for discovering which vaults + which one is default
    for the current identity. Resource reads (on_read_resource) have no
    such parameter to read (ReadResourceRequestParams carries a uri, not
    tool-style arguments) and always use the identity's default.

    Not covered here: /health doesn't go through MCP tool-call dispatch, so
    this middleware never runs for it — it stays scoped to
    Config.default_vault_name regardless of which identity is calling (it
    exposes no vault content, just liveness, so this doesn't leak anything).
    /attachments/* is also a custom route rather than tool-call dispatch,
    but does its own equivalent vault resolution by hand — see
    attachment_route below — since it's the one HTTP route that genuinely
    needs it.
    """

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        tool_name = getattr(context.message, "name", None)
        if tool_name in _canonical_arguments:
            supplied = set(getattr(context.message, "arguments", None) or {})
            if supplied - _canonical_arguments[tool_name]:
                return canonical_server.result({"error": {"code": "invalid_input", "message": "Unknown tool argument"}}, is_error=True)
        cfg = get_config()
        if not cfg.multi_vault:
            return await call_next(context)

        identity = _resolve_identity(cfg)
        if getattr(context.message, "name", None) == "list_vaults":
            # This identity-only discovery call does not touch vault content.
            # In particular, it must work for identities intentionally
            # configured with several vaults and no default.
            return await call_next(context)
        requested = None
        arguments = getattr(context.message, "arguments", None)
        if arguments:
            requested = arguments.get("vault")
        vault_name = _select_vault(identity, requested)

        token = set_current_vault(vault_name)
        try:
            return await call_next(context)
        finally:
            reset_current_vault(token)

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        cfg = get_config()
        if not cfg.multi_vault:
            return await call_next(context)

        identity = _resolve_identity(cfg)
        vault_name = _select_vault(identity, None)

        token = set_current_vault(vault_name)
        try:
            return await call_next(context)
        finally:
            reset_current_vault(token)


# Initialized in main() — empty at import time so the module can be imported
# without VAULT_PATH/VAULTS_CONFIG set (e.g. during testing or linting).
_cfg = None
_indices: dict[str, VaultIndex] = {}
_watchers: dict[str, VaultWatcher] = {}
_index = _CurrentVaultIndex()

mcp = FastMCP(name="obsidian-mcp", instructions=_load_instructions(), auth=_build_auth())
mcp.add_middleware(VaultResolutionMiddleware())


# Gates which optional plugin-format tool groups get
# registered below, so disabled tools never appear in the client's tool list.
# Deliberately not the real Config object (that needs VAULT_PATH,
# see _build_auth() above) — just the feature flags, read straight from the
# environment.
@dataclass(frozen=True)
class _FeatureFlags:
    enable_canvas: bool
    enable_excalidraw: bool
    enable_kanban: bool
    enable_bases: bool

    @classmethod
    def from_env(cls) -> _FeatureFlags:
        """Read named flags without constructing the full vault config."""
        def enabled(name: str) -> bool:
            return os.environ.get(name, "false").lower() in ("1", "true", "yes")

        return cls(
            enable_canvas=enabled("ENABLE_CANVAS"),
            enable_excalidraw=enabled("ENABLE_EXCALIDRAW"),
            enable_kanban=enabled("ENABLE_KANBAN"),
            enable_bases=enabled("ENABLE_BASES"),
        )


_feature_flags = _FeatureFlags.from_env()


# ── Prompts ───────────────────────────────────────────────────────────────────

@mcp.prompt()
def weekly_review() -> str:
    """Summarize the past week: overdue/due-soon tasks, daily note highlights."""
    return weekly_review_prompt()


@mcp.prompt()
def daily_note(date: str = "today") -> str:
    """Open or create a daily note, carrying over yesterday's open tasks.
    date: 'today' | 'yesterday' | 'YYYY-MM-DD'."""
    return daily_note_prompt(date=date)


# ── Read ──────────────────────────────────────────────────────────────────────


# ── Write ─────────────────────────────────────────────────────────────────────


# ── Query / Graph ─────────────────────────────────────────────────────────────


def _list_vaults() -> list[dict]:
    """List the vault(s) the current identity (API key or GitHub login) may
    access. Returns [{name, description, is_default}]. Call this at the
    start of a session whenever more than one vault comes back — pass
    vault=<name> on any other tool to operate on a non-default one for that
    single call; omit it to use whichever entry has is_default=true. In
    single-vault mode (no VAULTS_CONFIG) this always returns exactly one
    entry with is_default=true — there's nothing to choose between."""
    cfg = get_config()
    if not cfg.multi_vault:
        vault = cfg.vaults[cfg.default_vault_name]
        return [{"name": vault.name, "description": vault.description, "is_default": True}]

    identity = _resolve_identity(cfg)
    return [
        {
            "name": name,
            "description": cfg.vaults[name].description,
            "is_default": name == identity.default,
        }
        for name in identity.vaults
    ]


# ── Attachments ───────────────────────────────────────────────────────────────


async def _check_bearer_token(request: Request, cfg) -> AccessToken | None:
    """Returns the AccessToken for a valid Authorization: Bearer header, or
    None if missing/invalid. Manual verification, not FastMCP's own auth
    middleware — this is a @mcp.custom_route, which bypasses that pipeline
    entirely, so get_access_token() won't see anything for this request."""
    token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token:
        return None
    if cfg.api_key and hmac.compare_digest(token, cfg.api_key):
        return AccessToken(token=token, client_id=cfg.api_key, scopes=[])
    if mcp.auth is not None:
        return await mcp.auth.verify_token(token)
    return None


def _check_scoped_token(request: Request, cfg, method: str, path: str) -> str | None:
    """Returns the vault name a valid scoped token (?exp=&sig=[&vault=])
    grants access to for this exact path+method, or None if missing/
    invalid/expired. Tries every api_key identity's own value as the HMAC
    signing key in multi-vault mode (there's no bearer header here to say
    up front which identity minted it), or the single global API_KEY in
    single-vault mode. A key matching the signature but not actually
    allowed the requested vault is treated the same as no match."""
    exp = request.query_params.get("exp")
    sig = request.query_params.get("sig")
    if not exp or not sig:
        return None
    vault_param = request.query_params.get("vault", cfg.default_vault_name)

    if cfg.multi_vault:
        candidates = [(identity.value, identity) for identity in cfg.identities if identity.type == "api_key"]
    else:
        candidates = [(cfg.api_key, None)] if cfg.api_key else []

    for signing_key, identity in candidates:
        if not verify_attachment_token(signing_key, method, path, vault_param, exp, sig):
            continue
        if identity is not None and vault_param not in identity.vaults:
            continue
        return vault_param
    return None


@mcp.custom_route("/health", methods=["GET"])
async def health_route(request: Request) -> Response:
    """Unauthenticated liveness/readiness check for Docker HEALTHCHECK,
    uptime monitors, etc. Returns no vault content, so no auth is required.

    Returns {status: "starting"|"ok", index_ready, reconciliation telemetry}.
    Returns 503 until the initial index build and vault-wide reconciliation
    finish, then 200 while the built index remains usable. A later per-note
    reconciliation error is exposed as telemetry without discarding readiness.
    In multi-vault mode, telemetry is for Config.default_vault_name because
    this unauthenticated route exposes no per-identity vault details.
    """
    if _cfg is None or not _indices:
        return JSONResponse({"status": "starting"}, status_code=503)
    index = _indices[_cfg.default_vault_name]
    ready = index.is_ready()
    return JSONResponse(
        {
            "status": "ok" if ready else "starting",
            "index_ready": ready,
            **index.reconcile_status(),
        },
        status_code=200 if ready else 503,
    )


@mcp.custom_route("/attachments/{path:path}", methods=["PUT", "GET"])
async def attachment_route(request: Request) -> Response:
    """Direct binary upload/download, outside the MCP tool-call channel.

    add_attachment/read_attachment move file content as base64
    inside a tool call/result, which forces the bytes through whatever
    client/model is driving the MCP session — expensive and risky for large
    or many files. This route lets a client PUT/GET raw bytes straight to/from
    disk instead. Accepts the server's static bearer token, a valid GitHub
    OAuth access token (if configured), or a short-lived scoped token from
    the internal token helper (?exp=&sig=), so callers never need to be
    handed the long-lived master key.

    Usage:
        curl -X PUT --data-binary @file.png \\
            -H "Authorization: Bearer <API_KEY>" http://host:port/attachments/path/to/file.png
        curl -o file.png \\
            -H "Authorization: Bearer <API_KEY>" http://host:port/attachments/path/to/file.png

    Multi-vault mode: this route doesn't go through VaultResolutionMiddleware
    (it's a plain Starlette route, not MCP tool-call dispatch), so vault
    resolution is done by hand here — a bearer token resolves to its
    identity's default vault (override with ?vault=<name>, same rule as the
    vault= tool argument: must be one of that identity's allowed vaults); a
    scoped token from the internal token helper carries its vault baked
    into the signature already.
    """
    cfg = get_config()
    path = request.path_params["path"]
    method = request.method

    # Authenticate and choose a vault before any path-policy check so callers
    # cannot probe protected path boundaries through response differences.
    vault_name: str | None = None
    access_token = await _check_bearer_token(request, cfg)
    if access_token is not None:
        if cfg.multi_vault:
            try:
                identity = _identity_for_token(cfg, access_token)
                requested = request.query_params.get("vault")
                vault_name = _select_vault(identity, requested)
            except PermissionError as exc:
                return JSONResponse({"error": str(exc)}, status_code=403)
        else:
            vault_name = cfg.default_vault_name
    else:
        vault_name = _check_scoped_token(request, cfg, method, path)

    if vault_name is None:
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    context_token = set_current_vault(vault_name)
    try:
        storage = VaultStorage.from_config(cfg)
        try:
            path = validate_attachment_path(path, write=method == "PUT")
        except InvalidFileTypeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (VaultPathError, ReadPermissionError, WritePermissionError):
            return JSONResponse({"error": "forbidden"}, status_code=403)

        if method == "GET":
            try:
                data = storage.read_bytes(path)
            except FileNotFoundError:
                return JSONResponse({"error": "Attachment not found"}, status_code=404)
            mime, _ = mimetypes.guess_type(path)
            return Response(data, media_type=mime or "application/octet-stream")

        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError:
                return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
            if declared_length < 0:
                return JSONResponse({"error": "Invalid Content-Length"}, status_code=400)
            if declared_length > cfg.max_attachment_bytes:
                return JSONResponse({"error": "Attachment too large"}, status_code=413)

        chunks: list[bytes] = []
        received = 0
        async for chunk in request.stream():
            received += len(chunk)
            if received > cfg.max_attachment_bytes:
                return JSONResponse({"error": "Attachment too large"}, status_code=413)
            chunks.append(chunk)
        data = b"".join(chunks)
        try:
            result = write_attachment_bytes(path, data)
        except AttachmentTooLargeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=413)
        except (ValueError, InvalidFileTypeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (VaultPathError, WritePermissionError):
            return JSONResponse({"error": "forbidden"}, status_code=403)
        return JSONResponse(result)
    finally:
        reset_current_vault(context_token)


# ── Templates ─────────────────────────────────────────────────────────────────


# ── Canvas ────────────────────────────────────────────────────────────────────

if _feature_flags.enable_canvas:

    @mcp.tool()
    def list_canvases_tool(vault: str | None = None) -> list[str]:
        """List Canvas paths without reading their nodes or edges."""
        return list_canvases()

    @mcp.tool()
    def read_canvas_tool(path: str, vault: str | None = None) -> dict:
        """Read and return one complete Canvas structure.
        Returns {path, nodes: [...], edges: [...], revision}."""
        return read_canvas(path)

    @mcp.tool()
    @_mutation_boundary
    def write_canvas_tool(
        path: str,
        nodes: list[dict] | None = None,
        edges: list[dict] | None = None,
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create or fully overwrite a Canvas. Prefer patch_canvas_tool for
        targeted node/edge edits. Full-file mutation with no dry-run preview.
        Node fields: type ('text'|'file'|'group'|'link'), x, y, width, height.
        Text nodes: text. File nodes: file (vault path). Link nodes: url.
        Edge fields: fromNode, toNode, label (optional). IDs are auto-generated if omitted.
        Returns {path, status, nodes, edges, revision}."""
        return write_canvas(
            path,
            nodes=nodes,
            edges=edges,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def patch_canvas_tool(
        path: str,
        add_nodes: list[dict] | None = None,
        update_nodes: list[dict] | None = None,
        delete_node_ids: list[str] | None = None,
        add_edges: list[dict] | None = None,
        delete_edge_ids: list[str] | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Target node/edge changes in one existing Canvas. Prefer this over a
        full Canvas write when preserving unspecified structure. The tool reads
        and atomically rewrites one file; no dry-run preview.
        update_nodes: each dict must include 'id'. delete_node_ids also removes
        all edges connected to those nodes. Returns
        {path, status, nodes, edges, revision}."""
        return patch_canvas(
            path,
            add_nodes=add_nodes,
            update_nodes=update_nodes,
            delete_node_ids=delete_node_ids,
            add_edges=add_edges,
            delete_edge_ids=delete_edge_ids,
            expected_revision=expected_revision,
        )


# ── Excalidraw ────────────────────────────────────────────────────────────────

if _feature_flags.enable_excalidraw:

    @mcp.tool()
    def list_excalidraw_tool(vault: str | None = None) -> list[str]:
        """List Excalidraw paths without reading their scene data."""
        return list_excalidraw()

    @mcp.tool()
    def read_excalidraw_tool(path: str, vault: str | None = None) -> dict:
        """Read and return one complete Excalidraw scene.
        Returns {path, elements, app_state, files, revision}."""
        return read_excalidraw(path)

    @mcp.tool()
    @_mutation_boundary
    def write_excalidraw_tool(
        path: str,
        elements: list[dict] | None = None,
        app_state: dict | None = None,
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create or fully overwrite an Excalidraw scene. Prefer
        patch_excalidraw_tool for targeted element edits. Full-file mutation
        with no dry-run preview.
        Element fields: type ('rectangle'|'ellipse'|'text'|'arrow'|'freedraw'|...), x, y,
        width, height. Element 'id' is auto-generated if omitted.
        Returns {path, status, elements, revision}."""
        return write_excalidraw(
            path,
            elements=elements,
            app_state=app_state,
            index=_index,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def patch_excalidraw_tool(
        path: str,
        add_elements: list[dict] | None = None,
        update_elements: list[dict] | None = None,
        delete_element_ids: list[str] | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Target element changes in one existing Excalidraw scene. Prefer this
        over a full scene write when preserving unspecified elements. The tool
        reads and atomically rewrites one file; no dry-run preview.
        update_elements: each dict must include 'id'.
        Returns {path, status, elements, revision}."""
        return patch_excalidraw(
            path,
            add_elements=add_elements,
            update_elements=update_elements,
            delete_element_ids=delete_element_ids,
            index=_index,
            expected_revision=expected_revision,
        )


# ── Kanban ────────────────────────────────────────────────────────────────────

if _feature_flags.enable_kanban:

    @mcp.tool()
    def read_kanban_tool(path: str, vault: str | None = None) -> dict:
        """Read and return one complete Kanban board (requires kanban-plugin
        in frontmatter).
        Returns {path, plugin, columns: [...], total_cards, revision}."""
        return read_kanban(path)

    @mcp.tool()
    @_mutation_boundary
    def create_kanban_board_tool(
        path: str,
        columns: list[str],
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create or fully replace a Kanban board with the given columns.
        Prefer the card tools for changes to an existing board. Full-file
        mutation with no dry-run preview.
        Returns {path, status, columns, revision}."""
        return create_kanban_board(
            path,
            columns,
            index=_index,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def add_kanban_card_tool(
        path: str,
        column: str,
        text: str,
        done: bool = False,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Add one card without supplying the full board. Reads and atomically
        rewrites one board; no dry-run preview. The card is inserted at the top.
        Returns {path, status, column, card, done, revision}."""
        return add_kanban_card(
            path,
            column,
            text,
            done=done,
            index=_index,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    @_mutation_boundary
    def move_kanban_card_tool(
        path: str,
        card_text: str,
        from_column: str,
        to_column: str,
        done: bool | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Move one existing card without supplying the full board. Reads and
        atomically rewrites one board; no dry-run preview. done=true/false
        updates the tick state.
        Returns {path, status, card, from, to, revision}."""
        return move_kanban_card(
            path,
            card_text,
            from_column,
            to_column,
            done=done,
            index=_index,
            expected_revision=expected_revision,
        )

    @mcp.tool()
    @_mutation_boundary
    def delete_kanban_card_tool(
        path: str,
        card_text: str,
        column: str | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Delete one card without supplying the full board. Reads and
        atomically rewrites one board; no dry-run preview. column limits the
        search to one column.
        Returns {path, status, card, revision}."""
        return delete_kanban_card(
            path,
            card_text,
            column=column,
            index=_index,
            expected_revision=expected_revision,
        )


# ── Bases ─────────────────────────────────────────────────────────────────────

if _feature_flags.enable_bases:

    @mcp.tool()
    def list_bases_tool(vault: str | None = None) -> list[str]:
        """List Base paths without reading their definitions."""
        return list_bases()

    @mcp.tool()
    def read_base_tool(path: str, vault: str | None = None) -> dict:
        """Read and return one complete Base definition.
        Returns {path, filters, formulas, properties, views, revision}."""
        return read_base(path)

    @mcp.tool()
    @_mutation_boundary
    def write_base_tool(
        path: str,
        filters: dict | None = None,
        formulas: dict | None = None,
        properties: dict | None = None,
        views: list[dict] | None = None,
        expected_revision: str | None = None,
        create_only: bool = False,
        vault: str | None = None,
    ) -> dict:
        """Create or fully overwrite a Base. Prefer patch_base_tool for targeted
        formula, property, filter, or view edits. This full-file mutation also
        scans existing Bases for known property names; no dry-run preview.
        filters: boolean tree ({and:[...]}, {or:[...]}, {not:...}) or a single
        string statement, e.g. 'status != "done"' or 'file.hasTag("book")'.
        formulas: name -> expression string. properties: name -> {displayName}.
        views: list of {type, name, limit, filters, order, groupBy, summaries};
        'type' (e.g. 'table'|'cards'|'list') is required per view.
        Returns {path, status, views, known_properties, revision} — known_properties is
        collected from existing .base files in the vault to keep naming consistent."""
        return write_base(
            path,
            filters=filters,
            formulas=formulas,
            properties=properties,
            views=views,
            index=_index,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    @mcp.tool()
    @_mutation_boundary
    def patch_base_tool(
        path: str,
        update_formulas: dict | None = None,
        delete_formula_keys: list[str] | None = None,
        update_properties: dict | None = None,
        delete_property_keys: list[str] | None = None,
        set_filters: dict | None = None,
        add_views: list[dict] | None = None,
        update_views: list[dict] | None = None,
        delete_view_names: list[str] | None = None,
        expected_revision: str | None = None,
        vault: str | None = None,
    ) -> dict:
        """Target fields in one existing Base while preserving unspecified
        structure. The tool reads and atomically rewrites one file; no dry-run.
        update_formulas/update_properties are merged by key. set_filters replaces
        the whole filters block. update_views: each dict must include 'name'.
        Returns {path, status, views, revision}."""
        return patch_base(
            path,
            update_formulas=update_formulas,
            delete_formula_keys=delete_formula_keys,
            update_properties=update_properties,
            delete_property_keys=delete_property_keys,
            set_filters=set_filters,
            add_views=add_views,
            update_views=update_views,
            delete_view_names=delete_view_names,
            index=_index,
            expected_revision=expected_revision,
        )


# ── Folders ───────────────────────────────────────────────────────────────────


_canonical_arguments = canonical_server.register(mcp, _index, _list_vaults)


# ── MCP Resources ─────────────────────────────────────────────────────────────

@mcp.resource("vault://notes/{path}")
def vault_note_resource(path: str) -> str:
    """Raw content of a vault note — use as context without calling a tool."""
    try:
        return VaultStorage.from_config().read_text(path)
    except (ConfigError, FileNotFoundError, PermissionError, VaultPathError, OSError):
        return ""


# ── Startup ───────────────────────────────────────────────────────────────────

def main() -> None:
    global _cfg
    _cfg = get_config()
    for name, vault in _cfg.vaults.items():
        context_token = set_current_vault(name)
        try:
            policy = VaultAccessPolicy.from_config(_cfg)
        finally:
            reset_current_vault(context_token)
        VaultStorage(policy).probe_create_only_support()
        index = VaultIndex(vault.path, exclude_paths=vault.exclude_paths, policy=policy)
        watcher = VaultWatcher(
            vault.path,
            debounce_ms=_cfg.watcher_debounce_ms,
            reconcile_interval=_cfg.index_reconcile_interval,
            policy=policy,
        )
        _indices[name] = index
        _watchers[name] = watcher

        def initialize_index(
            index: VaultIndex = index,
            watcher: VaultWatcher = watcher,
            name: str = name,
        ) -> None:
            try:
                index.build(publish_ready=False)
                watcher.start(on_change=index.update, on_reconcile=index.reconcile)
                index.reconcile()
                index.mark_ready()
            except Exception:
                logger.exception("Initial index build/reconciliation failed for vault %s", name)

        threading.Thread(target=initialize_index, daemon=True).start()
    if _cfg.multi_vault:
        logger.info("Multi-vault mode: %d vault(s) configured (%s)", len(_cfg.vaults), ", ".join(_cfg.vaults))
    if _cfg.transport == "stdio":
        logger.info("Starting obsidian-mcp (transport=stdio)")
        mcp.run(transport=_cfg.transport)
    else:
        logger.info(
            "Starting obsidian-mcp (transport=%s, host=%s, port=%d)",
            _cfg.transport, _cfg.host, _cfg.port,
        )
        mcp.run(transport=_cfg.transport, host=_cfg.host, port=_cfg.port)


if __name__ == "__main__":
    main()
