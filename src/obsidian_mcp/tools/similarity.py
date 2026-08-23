"""Find conceptually related notes even when the wording differs, using a
lightweight TF-IDF + cosine-similarity heuristic — not a transformer
embedding model. Deliberate scope reduction: a real embedding model means a
new heavy dependency (torch/sentence-transformers) plus a vector index to
keep in sync, a poor fit for a small self-hosted server (e.g. on a
Raspberry Pi). TF-IDF over the vault's own vocabulary is a stdlib-only
approximation that still finds notes sharing distinctive words even when
the surface phrasing differs — it won't catch pure synonym rewrites the way
real embeddings would."""
from __future__ import annotations

import math
import re
from collections import Counter

from ..config import get_config
from ..domain.index import VaultIndex
from .query import _load_note

# Words of 3+ letters (Unicode-aware) — short function words carry little
# signal for "is this note about the same thing".
_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [w.lower() for w in _WORD_RE.findall(text)]


def _tf_idf_vector(term_counts: Counter, doc_freq: Counter, n_docs: int) -> dict[str, float]:
    vector: dict[str, float] = {}
    for term, count in term_counts.items():
        idf = math.log((n_docs + 1) / (doc_freq.get(term, 0) + 1)) + 1
        vector[term] = count * idf
    return vector


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    common = a.keys() & b.keys()
    if not common:
        return 0.0
    dot = sum(a[t] * b[t] for t in common)
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


def find_similar_notes(
    text: str,
    index: VaultIndex,
    limit: int = 5,
    exclude_path: str | None = None,
    min_score: float = 0.1,
) -> list[dict]:
    """Rank vault notes by TF-IDF cosine similarity to `text`, for duplicate
    prevention before creating a new note ("does this topic already exist
    under different wording?"). exclude_path: skip a note (e.g. the one
    being edited) from the results. min_score filters out noise-level
    matches (0-1 scale, higher = stricter). Returns [{path, score}],
    most similar first."""
    cfg = get_config()
    docs: dict[str, Counter] = {}
    for note_path in index.get_all_notes():
        if note_path == exclude_path:
            continue
        try:
            note = _load_note(cfg.vault_path, note_path)
        except Exception:
            continue
        tokens = _tokenize(note.content)
        if tokens:
            docs[note_path] = Counter(tokens)

    query_tf = Counter(_tokenize(text))
    if not query_tf or not docs:
        return []

    doc_freq: Counter = Counter()
    vocab = set(query_tf)
    for tf in docs.values():
        vocab |= tf.keys()
    for term in vocab:
        doc_freq[term] = sum(1 for tf in docs.values() if term in tf) + (1 if term in query_tf else 0)

    n_docs = len(docs) + 1  # + the query itself, treated as one more document
    query_vec = _tf_idf_vector(query_tf, doc_freq, n_docs)

    scored = [
        {"path": note_path, "score": round(_cosine_similarity(query_vec, _tf_idf_vector(tf, doc_freq, n_docs)), 4)}
        for note_path, tf in docs.items()
    ]
    scored = [s for s in scored if s["score"] >= min_score]
    scored.sort(key=lambda s: s["score"], reverse=True)
    return scored[:limit]
