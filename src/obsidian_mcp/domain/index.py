from __future__ import annotations

import logging
import stat
import threading
from collections import defaultdict
from pathlib import Path

from ..storage.filesystem import VaultStorage
from ..storage.policy import VaultAccessPolicy
from .parser import parse_note

logger = logging.getLogger(__name__)


class IndexBuildingError(Exception):
    pass


class VaultIndex:
    def __init__(
        self,
        vault_root: Path,
        exclude_paths: list[str] | None = None,
        policy: VaultAccessPolicy | None = None,
    ) -> None:
        self._vault_root = vault_root
        self._exclude_paths = exclude_paths or []
        self._policy = policy or VaultAccessPolicy(vault_root)
        self._storage = VaultStorage(self._policy)
        self._lock = threading.Lock()
        self._ready = False
        self._backlinks: dict[str, set[str]] = defaultdict(set)
        self._tags_index: dict[str, set[str]] = defaultdict(set)
        self._outlinks: dict[str, set[str]] = {}
        self._alias_index: dict[str, str] = {}        # alias_lower → real_path
        self._block_index: dict[str, dict[str, int]] = {}  # path → {block_id → line}
        self._all_notes: set[str] = set()

    def build(self) -> None:
        # Discover through the storage gateway.  In addition to applying the
        # read policy this walks directories with O_NOFOLLOW, so a denied or
        # symlinked subtree is never fed to the indexer.
        md_files = [
            path
            for path in self._storage.list_files()
            if path.relative.lower().endswith(".md") and not self._is_excluded(path.relative)
        ]
        with self._lock:
            # Mark not-ready before clearing so concurrent readers see IndexBuildingError
            # rather than empty results during a rebuild.
            self._ready = False
            self._backlinks.clear()
            self._tags_index.clear()
            self._outlinks.clear()
            self._alias_index.clear()
            self._block_index.clear()
            self._all_notes.clear()

        for md_file in md_files:
            rel = md_file.relative
            try:
                raw = self._storage.read_text(rel)
                note = parse_note(raw, path=rel)
                self._index_note(rel, note)
            except Exception:
                logger.exception("Failed to index %s", rel)

        with self._lock:
            self._ready = True
        logger.info("VaultIndex ready – %d notes indexed", len(self._all_notes))

    def update(self, path: str) -> None:
        try:
            rel = self._policy.canonicalize(path).relative
        except Exception:
            self.remove(path)
            return
        # Purge the old canonical entry before checking the new filesystem
        # state. This is important when a readable note is renamed into a
        # denied subtree: the watcher may report only the destination path.
        self.remove(rel)
        if not self._policy.can_read(rel) or self._is_excluded(rel) or not self._storage.exists(rel):
            self._remove_descendants(rel)
            return
        try:
            info = self._storage.stat(rel)
            if stat.S_ISDIR(info.st_mode):
                for candidate in self._storage.list_files(rel):
                    if candidate.relative.lower().endswith(".md") and not self._is_excluded(candidate.relative):
                        self.update(candidate.relative)
                return
            raw = self._storage.read_text(rel)
            note = parse_note(raw, path=rel)
            self.remove(rel)
            self._index_note(rel, note)
        except Exception:
            logger.exception("Failed to update index for %s", path)

    def _remove_descendants(self, path: str) -> None:
        prefix = path.rstrip("/") + "/"
        with self._lock:
            stale = [note for note in self._all_notes if note.startswith(prefix)]
        for note in stale:
            self.remove(note)

    def remove(self, path: str) -> None:
        with self._lock:
            old_targets = self._outlinks.pop(path, set())
            for target in old_targets:
                self._backlinks[target].discard(path)
            for tag_set in self._tags_index.values():
                tag_set.discard(path)
            # Remove aliases pointing to this path
            stale = [k for k, v in self._alias_index.items() if v == path]
            for k in stale:
                del self._alias_index[k]
            self._block_index.pop(path, None)
            self._all_notes.discard(path)

    def get_backlinks(self, note_path: str) -> list[str]:
        self._assert_ready()
        with self._lock:
            stem = Path(note_path).stem
            path_lower = note_path.lower()
            # Strip .md: covers [[Folder/Note]] style links stored as "folder/note"
            path_no_ext = path_lower[:-3] if path_lower.endswith(".md") else path_lower
            keys = {path_lower, path_no_ext, stem.lower()}
            for alias_lower, real in self._alias_index.items():
                if real == note_path:
                    keys.add(alias_lower)
            results: set[str] = set()
            for key in keys:
                results.update(self._backlinks.get(key, set()))
            return sorted(results)

    def get_notes_by_tag(self, tag: str) -> list[str]:
        self._assert_ready()
        with self._lock:
            return sorted(self._tags_index.get(tag, set()))

    def get_all_tags_with_counts(self) -> dict[str, int]:
        self._assert_ready()
        with self._lock:
            return {tag: len(notes) for tag, notes in self._tags_index.items()}

    def get_all_notes(self) -> set[str]:
        self._assert_ready()
        with self._lock:
            return set(self._all_notes)

    def resolve_alias(self, name: str) -> str | None:
        with self._lock:
            return self._resolve(name)

    def get_block(self, path: str, block_id: str) -> int | None:
        self._assert_ready()
        with self._lock:
            return self._block_index.get(path, {}).get(block_id)

    def get_tag_tree(self) -> dict:
        self._assert_ready()
        with self._lock:
            tree: dict = {}
            for tag, notes in self._tags_index.items():
                parts = tag.split("/")
                node = tree
                for part in parts[:-1]:
                    node = node.setdefault(part, {})
                leaf = parts[-1]
                if leaf not in node:
                    node[leaf] = []
                if isinstance(node[leaf], dict):
                    node[leaf].setdefault("_notes", []).extend(sorted(notes))
                else:
                    node[leaf] = sorted(notes)
            return tree

    def get_outlinks(self, path: str) -> set[str]:
        self._assert_ready()
        with self._lock:
            return set(self._outlinks.get(path, set()))

    def is_ready(self) -> bool:
        return self._ready

    def _index_note(self, path: str, note) -> None:
        stem = Path(path).stem
        targets: set[str] = set()
        for link in note.wikilinks:
            targets.add(link.target)

        with self._lock:
            self._all_notes.add(path)
            # Normalize targets to lowercase for case-insensitive resolution
            targets_lower = {t.lower() for t in targets}
            self._outlinks[path] = targets_lower
            for target in targets_lower:
                self._backlinks[target].add(path)
            for tag in note.tags:
                self._tags_index[tag].add(path)
            # Register aliases: note stem itself + explicit aliases
            self._alias_index[stem.lower()] = path
            for alias in note.aliases:
                self._alias_index[alias.lower()] = path
            # Block index
            if note.block_refs:
                self._block_index[path] = {r.block_id: r.line for r in note.block_refs}

    def has_note(self, target: str) -> bool:
        """Return True if target resolves to a known note (by stem, path, alias, or path-without-ext)."""
        self._assert_ready()
        target_lower = target.lower()
        with self._lock:
            if target_lower in self._alias_index:
                return True
            for p in self._all_notes:
                p_lower = p.lower()
                if p_lower == target_lower:
                    return True
                if p_lower.endswith(".md") and p_lower[:-3] == target_lower:
                    return True
            return False

    def _resolve(self, name: str) -> str | None:
        """Resolve a name/alias to a real path. Returns None if not found."""
        stem = Path(name).stem
        return self._alias_index.get(stem.lower())

    def _assert_ready(self) -> None:
        if not self._ready:
            raise IndexBuildingError("Index is still being built – try again shortly")

    def _is_excluded(self, path: str | Path) -> bool:
        if isinstance(path, Path):
            rel = "/".join(path.relative_to(self._vault_root).parts)
            name = path.name
        else:
            rel = path.replace("\\", "/").strip("/")
            name = Path(rel).name
        if any(rel == rule or rel.startswith(rule.rstrip("/") + "/") for rule in self._exclude_paths):
            return True
        # Excalidraw files are *.md so they'd otherwise get tag/wikilink-parsed
        # as regular notes — their body is an embedded JSON scene, not prose,
        # and things like "#ffffff" hex colors would show up as bogus tags.
        return name.endswith(".excalidraw.md")
