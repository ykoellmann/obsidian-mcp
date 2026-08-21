from __future__ import annotations

from obsidian_mcp.tools.similarity import find_similar_notes


def test_finds_conceptually_related_note_by_shared_vocabulary(vault_factory):
    idx = vault_factory({
        "tgv-1369-reihenfolge.md": (
            "Die Reihenfolge der Warenträger im Regal muss der Airfom-Vorgabe "
            "entsprechen, sonst stimmt die Shopmöblierung nicht."
        ),
        "unrelated.md": "Rezept für Bananenbrot mit Walnüssen und Zimt.",
    })
    results = find_similar_notes("Airfom Reihenfolge Warenträger Shopmöblierung", idx)
    assert results
    assert results[0]["path"] == "tgv-1369-reihenfolge.md"


def test_unrelated_text_scores_low_or_excluded(vault_factory):
    idx = vault_factory({
        "cooking.md": "Ein Rezept für Bananenbrot mit Walnüssen und Zimt und Zucker.",
    })
    results = find_similar_notes("Quantenphysik Relativitätstheorie Elementarteilchen", idx)
    assert results == []


def test_exclude_path_omits_note(vault_factory):
    idx = vault_factory({
        "a.md": "Projektplanung für das neue Feature.",
        "b.md": "Projektplanung für ein anderes Feature.",
    })
    results = find_similar_notes("Projektplanung Feature", idx, exclude_path="a.md")
    assert all(r["path"] != "a.md" for r in results)


def test_limit_caps_results(vault_factory):
    idx = vault_factory({
        f"note{i}.md": "Projektplanung Feature Roadmap Meilenstein" for i in range(10)
    })
    results = find_similar_notes("Projektplanung Feature Roadmap", idx, limit=3)
    assert len(results) <= 3


def test_empty_query_returns_empty(vault_factory):
    idx = vault_factory({"a.md": "Some content here."})
    assert find_similar_notes("", idx) == []


def test_empty_vault_returns_empty(vault_factory):
    idx = vault_factory({})
    assert find_similar_notes("anything", idx) == []


def test_results_sorted_descending_by_score(vault_factory):
    idx = vault_factory({
        "close.md": "Projektplanung Feature Roadmap Meilenstein Budget",
        "far.md": "Projektplanung erwähnt am Rande, sonst geht es um Katzen und Gärten.",
    })
    results = find_similar_notes("Projektplanung Feature Roadmap Meilenstein Budget", idx)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)
