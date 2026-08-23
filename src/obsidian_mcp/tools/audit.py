from __future__ import annotations

from ..config import get_config
from ..storage import audit as audit_storage
from ..storage.policy import VaultAccessPolicy


def log_write(tool: str, path: str | None, summary: str) -> None:
    """Append one audit entry for a write-tool call. Best-effort: a logging
    failure must never block or fail the write it's recording."""
    try:
        cfg = get_config()
        vault = cfg.resolve_vault_name()
        audit_storage.append_entry(
            cfg.audit_log_path,
            cfg.lock_path,
            tool=tool,
            path=path,
            summary=summary,
            vault=vault,
        )
    except Exception:
        pass


def get_audit_log(
    path: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Query the append-only write-action audit log, most recent first.
    path/tool/since are optional filters (since: ISO timestamp, inclusive)."""
    cfg = get_config()
    vault = cfg.resolve_vault_name()
    policy = VaultAccessPolicy.from_config(cfg)
    canonical_path = policy.resolve_read(path).relative if path is not None else None
    return audit_storage.read_entries(
        cfg.audit_log_path,
        path=canonical_path,
        tool=tool,
        since=since,
        limit=limit,
        vault=vault,
        path_allowed=lambda entry_path: entry_path is None or policy.can_read(entry_path),
    )


def get_note_history(path: str, limit: int = 20) -> list[dict]:
    """Audit entries for one specific note, most recent first."""
    cfg = get_config()
    vault = cfg.resolve_vault_name()
    policy = VaultAccessPolicy.from_config(cfg)
    canonical_path = policy.resolve_read(path).relative
    return audit_storage.read_entries(
        cfg.audit_log_path,
        path=canonical_path,
        limit=limit,
        vault=vault,
        path_allowed=lambda entry_path: entry_path is None or policy.can_read(entry_path),
    )
