# Feature-Roadmap – second-brain-mcp

Ziel: vollständige Obsidian-Unterstützung und darüber hinaus.

---

## Phase 2 – Enhanced Core (nächste Schritte)

### 2.1 Search mit Snippets & Ranking
**Tool:** `search_notes_tool` erweitern

Aktuell: gibt nur Pfad + Tags zurück, kein Kontext.

```python
# Neu: strukturierte Ergebnisse
{
  "path": "Notizen/Python-Tipps.md",
  "score": 0.92,
  "snippets": [
    {"line": 23, "text": "...`filelock` verhält sich anders wenn der **Lock-File** auf einem NFS-Mount..."}
  ],
  "tags": ["konzept/programmierung"]
}
```

- Ranking: exakter Treffer > Wort-Anfang > Teilstring
- Snippets: ±2 Zeilen um den Treffer, Match bold-markiert
- Optional: `regex=True` für Regex-Suche
- Optional: `limit=20` für Ergebnis-Anzahl

### 2.2 `move_note` – Umbenennen + Backlink-Rewriting
**Neues Tool**

```python
@mcp.tool()
def move_note(from_path: str, to_path: str) -> dict:
    # 1. Datei verschieben (atomar)
    # 2. Alle .md-Dateien im Vault scannen
    # 3. [[from_path]]-Wikilinks auf [[to_path]] umschreiben
    # 4. Index aktualisieren
    # Gibt zurück: {moved: ..., updated_links_in: [...]}
```

### 2.3 `get_orphans` – Notizen ohne Backlinks
**Neues Tool**

```python
@mcp.tool()
def get_orphans(exclude_folders: list[str] = ["Journal", "Templates"]) -> list[str]:
    # Notizen die von keiner anderen Notiz verlinkt werden
    # Journal-Einträge standardmäßig ausgeschlossen
```

### 2.4 `get_broken_links` – tote Wikilinks finden
**Neues Tool**

```python
@mcp.tool()
def get_broken_links() -> list[dict]:
    # [{source: "Notizen/X.md", link: "[[NichtExistent]]", line: 12}]
    # Prüft gegen: existierende Dateien + bekannte Aliases
```

### 2.5 `list_notes_with_meta` – Listing mit Metadaten
**`list_notes_tool` Erweiterung**

```python
@mcp.tool()
def list_notes_tool(folder: str = "", include_meta: bool = False) -> list[str | dict]:
    # include_meta=True: [{path, title, tags, status, created, mtime}]
```

---

## Phase 3 – Graph & Navigation

### 3.1 `get_link_graph` – traversierbarer Link-Graph
**Neues Tool – das wichtigste dieser Phase**

```python
@mcp.tool()
def get_link_graph(
    root: str,          # Startnotiz
    depth: int = 2,     # wie viele Ebenen ausgehend
    direction: str = "both",  # "outgoing" | "incoming" | "both"
    include_tags: bool = False
) -> dict:
    # {
    #   "root": "Projekte/second-brain-mcp.md",
    #   "nodes": [{"path": ..., "title": ..., "tags": [...]}],
    #   "edges": [{"from": ..., "to": ..., "type": "wikilink"}]
    # }
```

Erlaubt: „Zeig mir alles was mit diesem Projekt zusammenhängt (2 Ebenen tief)".

### 3.2 `get_tag_tree` – verschachtelter Tag-Baum
**Neues Tool**

```python
@mcp.tool()
def get_tag_tree() -> dict:
    # {
    #   "konzept": {
    #     "mcp": ["Notizen/MCP-Protokoll.md"],
    #     "ki": {
    #       "llm": ["Notizen/Claude.md"]
    #     }
    #   },
    #   "projekt": {
    #     "aktiv": ["Projekte/second-brain-mcp.md"]
    #   }
    # }
```

### 3.3 `get_vault_stats` – Vault-Statistiken
**Neues Tool**

```python
@mcp.tool()
def get_vault_stats() -> dict:
    # {total_notes, total_links, total_tags, orphans_count,
    #  broken_links_count, most_linked: [...], most_linking: [...]}
```

---

## Phase 4 – Vollständige Obsidian-Feature-Unterstützung

### 4.1 Block-Referenzen
**Parser + Index erweitern**

- Parsen von `^block-id`-Ankern in Notizen
- Auflösen von `[[Note^block-id]]`-Links
- Neues Tool: `read_block(path, block_id) -> str`
- Index: `block_index[note_path][block_id] = line_number`

### 4.2 Aliases – transparente Auflösung
**Parser + Index erweitern**

- `aliases:` aus Frontmatter in Index aufnehmen
- `[[Alias]]` → automatisch zur echten Notiz auflösen
- `get_broken_links` berücksichtigt Aliases (kein false positive)
- Neues Tool: `resolve_alias(name) -> str | None`

### 4.3 Embedded Notes – `![[Note]]` Transclusion
**Parser + neues Tool**

- `![[Note]]` und `![[Note#Section]]` parsen
- Neues Tool: `render_note(path) -> str` – löst alle Embeddings auf,
  gibt vollständigen Text zurück (für vollständigen Kontext an Claude)

### 4.4 Daily Notes
**Neues Tool**

```python
@mcp.tool()
def get_daily_note(date: str = "today") -> dict:
    # date: "today" | "yesterday" | "YYYY-MM-DD"
    # Sucht in Journal/ nach YYYY-MM-DD.md
    # Erstellt aus Template wenn nicht vorhanden

@mcp.tool()
def get_daily_notes_range(from_date: str, to_date: str) -> list[dict]:
    # Alle Daily Notes im Zeitraum, mit Aufgaben-Summary
```

### 4.5 Task-Extraktion quer über den Vault
**Neues Tool**

```python
@mcp.tool()
def get_tasks(
    status: str = "open",  # "open" | "done" | "all"
    folder: str = "",
    tag: str | None = None
) -> list[dict]:
    # [{text, done, source_path, line, due_date (aus [[due::YYYY-MM-DD]])}]
```

### 4.6 Callout-Parsing
**Parser erweitern**

- Callout-Typen erkennen: `[!NOTE]`, `[!WARNING]`, `[!TIP]`, `[!IMPORTANT]`, `[!QUESTION]`
- In `Note`-Dataclass aufnehmen: `callouts: list[Callout]`
- `read_note` gibt Callouts strukturiert zurück

### 4.7 Canvas-Support (`.canvas`-Dateien)
**Neues Tool**

Obsidian Canvas = JSON-Format mit Nodes und Edges.

```python
@mcp.tool()
def read_canvas(path: str) -> dict:
    # {nodes: [{id, type, text/file, x, y}], edges: [{from, to, label}]}

@mcp.tool()
def list_canvases() -> list[str]:
```

---

## Phase 5 – Erweiterte Suche & Abfragen

### 5.1 Dataview-ähnliche Abfragen
**Neues Tool**

```python
@mcp.tool()
def query_notes(
    tags: list[str] | None = None,
    status: str | None = None,
    frontmatter_filter: dict | None = None,  # {"prioritaet": {"gte": 3}}
    sort_by: str = "mtime",
    limit: int = 50
) -> list[dict]:
    # Filtert Notizen nach Metadaten ohne Volltextsuche
```

### 5.2 Fuzzy- und Regex-Suche
**`search_notes_tool` erweitern**

- `mode: "exact" | "fuzzy" | "regex"`
- Fuzzy: Levenshtein-Distanz, für Tippfehler-Toleranz

### 5.3 Template-Engine
**Neues Tool**

```python
@mcp.tool()
def create_from_template(
    template_path: str,
    output_path: str,
    variables: dict  # {"title": "Mein Projekt", "date": "2026-07-23"}
) -> dict:
    # Ersetzt {{title}}, {{date}} etc. im Template
    # Erstellt Zieldatei atomar
```

---

## Build-Order

```
Phase 2: 2.4 get_broken_links → 2.3 get_orphans → 2.1 Search+Snippets → 2.2 move_note → 2.5 list_meta
Phase 3: 4.2 Aliases (braucht Index) → 3.1 get_link_graph → 3.2 get_tag_tree → 3.3 vault_stats
Phase 4: 4.1 Block-Refs → 4.3 Embeddings → 4.4 Daily Notes → 4.5 Tasks → 4.6 Callouts → 4.7 Canvas
Phase 5: 5.1 Query → 5.2 Fuzzy → 5.3 Templates
```

Aliases (4.2) vor dem Link-Graph (3.1), weil der Graph Aliases für korrekte
Auflösung braucht. Block-Refs (4.1) vor Embeddings (4.3), weil Embeddings
Block-Refs auflösen müssen.
