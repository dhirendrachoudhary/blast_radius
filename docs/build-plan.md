# Blast Radius Code Analyzer — Build Plan
> Self-contained. No CGC. No FalkorDB. No external services.
> Stack: tree-sitter-language-pack + SQLite + Gemini (Vertex AI).

---

## Architecture Decision Log

| Decision | Rationale |
|---|---|
| Drop `codegraphcontext` dependency | Avoid version-pinning cascade; own the parsing logic outright |
| Drop `falkordb` dependency | Embedded SQLite replaces a daemon + Unix socket; zero ops overhead |
| Use `tree-sitter-language-pack==0.6.0` directly | Same library CGC uses internally; v1.x installs as `_native` and breaks the API |
| Pull parser logic from CGC source | `tree_sitter_manager.py` + `languages/python.py` are clean and isolated — adapted, not imported |
| SQLite recursive CTE for graph traversal | Equivalent expressiveness to Cypher `[:CALLS*1..10]`; single file, no server |

---

## Graph Schema (SQLite)

Two tables form the call graph:

```sql
CREATE TABLE functions (
    uid         TEXT PRIMARY KEY,           -- "file_path::name::line_start"
    name        TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    line_start  INTEGER NOT NULL,
    line_end    INTEGER NOT NULL,
    source      TEXT,
    complexity  INTEGER DEFAULT 0,
    decorators  TEXT,                       -- JSON list
    docstring   TEXT,
    repo_path   TEXT NOT NULL
);

CREATE TABLE calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    caller_uid  TEXT NOT NULL REFERENCES functions(uid),
    callee_name TEXT NOT NULL,
    callee_uid  TEXT REFERENCES functions(uid),  -- NULL until resolved in pass 2
    line_number INTEGER
);

CREATE INDEX idx_fn_name        ON functions(name);
CREATE INDEX idx_fn_file_lines  ON functions(file_path, line_start, line_end);
CREATE INDEX idx_calls_caller   ON calls(caller_uid);
CREATE INDEX idx_calls_callee   ON calls(callee_uid);
```

### Blast Radius Query (recursive CTE)

```sql
WITH RECURSIVE blast_radius(uid, depth) AS (
    SELECT uid, 0 FROM functions WHERE name IN ($changed_names)
    UNION ALL
    SELECT c.caller_uid, br.depth + 1
    FROM calls c
    JOIN blast_radius br ON br.uid = c.callee_uid
    WHERE br.depth < 10
)
SELECT DISTINCT f.* FROM functions f
JOIN blast_radius br ON br.uid = f.uid;
```

Entry points (nothing calls them):
```sql
SELECT f.* FROM functions f
WHERE f.uid IN (SELECT uid FROM blast_radius)
  AND f.uid NOT IN (SELECT caller_uid FROM calls WHERE callee_uid IS NOT NULL);
```

SQLite handles cycles natively in recursive CTEs — no explicit visited-set needed.

---

## What We Pull from CGC Source

These two files are adapted (not imported) from the [CGC repo](https://github.com/Unix-Dev-Ops/Code-Graph-Context):

| CGC file | Our file | What we keep |
|---|---|---|
| `utils/tree_sitter_manager.py` | `src/blast_radius/parser/tree_sitter_manager.py` | Full file; strip CGC imports only |
| `tools/languages/python.py` | `src/blast_radius/parser/python.py` | `PY_QUERIES` dict, `_find_functions()`, `_find_calls()`, `pre_scan_python()` — strip all DB/Neo4j code |
| `tools/graph_builder.py` | `src/blast_radius/parser/__init__.py` | `TreeSitterParser` wrapper (10 lines) only |

Everything else (FalkorDB worker, job system, MCP server, watcher) is not used.

---

## Project Structure

```
src/blast_radius/
├── parser/
│   ├── __init__.py             # TreeSitterParser dispatcher (from graph_builder.py)
│   ├── tree_sitter_manager.py  # language/parser loader (from CGC utils/)
│   └── python.py               # PY_QUERIES + extraction logic (from CGC languages/)
├── indexer.py                  # walks repo → parser → writes SQLite graph
├── graph.py                    # SQLite client + recursive CTE queries
├── git_diff.py                 # subprocess git diff → ChangedRange objects
├── resolver.py                 # ChangedRange → FunctionNode via graph lookup
├── synthesizer.py              # Gemini prompt builder + test code generator
├── runner.py                   # write test file, execute pytest, capture results
├── telemetry.py                # SQLite interaction logger (training data flywheel)
└── __main__.py                 # CLI entrypoint (typer) — index + analyze commands
```

---

## Phase 1 — Environment Setup
**Goal:** Clean Python environment with tree-sitter confirmed working.
**Time estimate: 20 min**

### 1.1 pyproject.toml dependencies

```toml
[project]
name = "blast-radius"
version = "0.1.0"
requires-python = ">=3.10"
dependencies = [
    "tree-sitter>=0.21.0",
    "tree-sitter-language-pack==0.6.0",
    "google-cloud-aiplatform",
    "python-dotenv",
    "pytest",
    "pytest-json-report",
    "typer",
]
```

### 1.2 .env

```env
GEMINI_PROJECT=your-gcp-project
GEMINI_LOCATION=us-central1
BLAST_RADIUS_DB=data/blast_radius.db
```

### 1.3 Smoke test — confirm tree-sitter parses Python

```python
from tree_sitter_language_pack import get_language, get_parser
parser = get_parser("python")
tree = parser.parse(b"def hello(): pass")
print(tree.root_node.sexp())  # should print the AST
```

---

## Phase 2 — Parser (adapted from CGC)
**Goal:** tree-sitter → structured `ParsedFile` with functions and call edges.
**Time estimate: 2 hours**

### 2.1 `tree_sitter_manager.py` (adapt from CGC)

Provides thread-safe language loading and `execute_query()` backward-compat shim.
Strip only the CGC-specific import paths. Everything else is kept as-is.

### 2.2 `PY_QUERIES` (adapt from CGC `languages/python.py`)

The query dictionary covers:
- `functions` — `function_definition` nodes: name, params, decorators, body
- `calls` — `call` nodes: identifier and attribute calls
- `imports` — `import_statement` and `import_from_statement`
- `lambdas` — lambda assignments that act as named functions

### 2.3 `_find_functions(tree, source_bytes, file_path)` → `list[FunctionNode]`

Extracts per function:
- `name`, `file_path`, `line_start`, `line_end`
- `source` — slice of `source_bytes` by line range
- `complexity` — count of `if/for/while/try` nodes in function body
- `decorators` — list of decorator name strings
- `docstring` — first string expression in body if present
- `uid` — `f"{file_path}::{name}::{line_start}"`

### 2.4 `_find_calls(tree, source_bytes, functions)` → `list[CallEdge]`

Per call node:
- `caller_uid` — which function contains this call (by line range lookup)
- `callee_name` — the called function's name (unresolved at this stage)
- `line_number` — line of the call expression

### 2.5 `ParsedFile` dataclass

```python
@dataclass
class ParsedFile:
    path: str
    functions: list[FunctionNode]
    calls: list[CallEdge]
```

---

## Phase 3 — Indexer (`indexer.py`)
**Goal:** Walk a repo, parse every `.py` file, write the call graph to SQLite.
**Time estimate: 1.5 hours**

### 3.1 Two-pass strategy

**Pass 1 — parse all files:**
```python
for py_file in repo_path.rglob("*.py"):
    parsed = parser.parse(py_file)
    db.insert_functions(parsed.functions)
    db.insert_calls_unresolved(parsed.calls)   # callee_uid = NULL
```

**Pass 2 — resolve callee names to UIDs:**
```python
for call in db.get_unresolved_calls():
    uid = db.find_function_uid_by_name(call.callee_name)
    if uid:
        db.resolve_call(call.id, uid)
```

Two passes are required because a function may be called before it is defined (across files).

### 3.2 What to skip

```python
SKIP_DIRS = {".venv", "venv", "__pycache__", ".git", "node_modules", "dist", "build"}
```

### 3.3 CLI command

```bash
python -m blast_radius index /path/to/repo
# Writes to $BLAST_RADIUS_DB (default: data/blast_radius.db)
```

---

## Phase 4 — Graph Client (`graph.py`)
**Goal:** SQLite wrapper with all blast-radius queries.
**Time estimate: 1 hour**

Replaces FalkorDB entirely. All queries are plain SQLite — no Cypher.

### 4.1 `FunctionNode` and `BlastRadiusResult` dataclasses

```python
@dataclass
class FunctionNode:
    uid: str
    name: str
    file_path: str
    line_start: int
    line_end: int
    source: str
    complexity: int

@dataclass
class BlastRadiusResult:
    ground_zero: list[FunctionNode]
    affected_functions: list[FunctionNode]
    entry_points: list[FunctionNode]
    call_chains: list[list[str]]
```

### 4.2 Core methods

| Method | Query strategy |
|---|---|
| `find_by_name(name)` | `SELECT * FROM functions WHERE name = ?` |
| `find_by_line(file_path, line)` | `WHERE file_path=? AND line_start<=? AND line_end>=?` |
| `get_blast_radius(fn_names)` | Recursive CTE walking `calls` backwards |
| `get_entry_points(uids)` | Filter blast radius set: no callers in `calls.callee_uid` |
| `get_call_chains(fn_names)` | Recursive CTE with path accumulation |

### 4.3 Call chain accumulation (SQLite)

```sql
WITH RECURSIVE chains(uid, name, path_str, depth) AS (
    SELECT uid, name, name, 0 FROM functions WHERE name IN ($changed)
    UNION ALL
    SELECT f.uid, f.name, c2.name || ' → ' || ch.path_str, ch.depth + 1
    FROM functions f
    JOIN calls c2 ON c2.caller_uid = f.uid
    JOIN chains ch ON ch.uid = c2.callee_uid
    WHERE ch.depth < 10
)
SELECT path_str FROM chains WHERE depth > 0;
```

---

## Phase 5 — Git Diff Resolver (`git_diff.py` + `resolver.py`)
**Goal:** Map `git diff` output to `FunctionNode` objects via the graph.
**Time estimate: 1.5 hours**

### 5.1 Parse the diff (`git_diff.py`)

```python
@dataclass
class ChangedRange:
    file_path: str      # absolute path
    lines: list[int]    # every changed line number

def get_changed_ranges(repo_path: str) -> list[ChangedRange]:
    result = subprocess.run(
        ["git", "diff", "--unified=0", "HEAD"],
        cwd=repo_path, capture_output=True, text=True
    )
    return _parse_unified_diff(result.stdout, repo_path)
```

Parse `@@ -a,b +c,d @@` hunk headers — expand ranges to individual line numbers.

**Edge cases:**
- New files (no `-` side) — treat all lines as changed
- Renamed files — use `git diff --find-renames`

### 5.2 Resolve lines to nodes (`resolver.py`)

```python
def resolve_to_functions(changed_ranges, graph) -> list[FunctionNode]:
    found = []
    for change in changed_ranges:
        for line in set(change.lines):
            node = graph.find_by_line(change.file_path, line)
            if node and node not in found:
                found.append(node)
    return found
```

**Fuzzy fallback:** if exact line intersection misses, widen to ±3 lines. Log when this fires — it means the index is stale and `blast-radius index` should be re-run.

---

## Phase 6 — Agentic Test Synthesizer (`synthesizer.py` + `runner.py`)
**Goal:** Blast radius subgraph → Gemini → runnable pytest → executed.
**Time estimate: 2 hours**

### 6.1 Context assembly (`synthesizer.py`)

`source` is stored on every `FunctionNode` — no filesystem reads:

```python
def build_context(result: BlastRadiusResult) -> str:
    parts = []
    parts.append("## Changed Functions (Ground Zero)")
    for fn in result.ground_zero:
        parts.append(f"### `{fn.name}` ({fn.file_path}:{fn.line_start})")
        parts.append(f"```python\n{fn.source}\n```")

    parts.append("## Affected Functions (Blast Radius)")
    for fn in result.affected_functions:
        parts.append(f"### `{fn.name}`")
        parts.append(f"```python\n{fn.source}\n```")

    parts.append("## Entry Points (API Surface)")
    for fn in result.entry_points:
        parts.append(f"- `{fn.name}`")

    parts.append("## Call Chains")
    for chain in result.call_chains:
        parts.append(f"- {' → '.join(chain)}")

    return "\n\n".join(parts)
```

Cap `affected_functions` to top 20 by `complexity` before building context — prevents Gemini context overflow on large repos.

### 6.2 Prompt

```python
SYSTEM_PROMPT = """
You are a senior Python test engineer. You receive:
1. Functions recently modified (Ground Zero)
2. All upstream functions impacted by those changes (Blast Radius)
3. The API entry points that surface those changes to callers
4. Call chains connecting them

Write pytest functions targeting the ENTRY POINTS.
Rules:
- pytest only. No unittest.
- Mock all external I/O (database, HTTP, filesystem).
- One function per behavior/edge case.
- Happy path + at least one failure case per entry point.
- Output ONLY valid Python. No markdown fences, no explanation.
"""
```

### 6.3 Gemini integration

```python
import vertexai
from vertexai.generative_models import GenerativeModel

def synthesize_tests(context: str) -> str:
    vertexai.init(project=os.getenv("GEMINI_PROJECT"), location=os.getenv("GEMINI_LOCATION"))
    model = GenerativeModel("gemini-2.0-flash-001")
    response = model.generate_content([SYSTEM_PROMPT, context])
    return response.text.strip()
```

### 6.4 Test runner (`runner.py`)

1. Strip markdown fences from Gemini output
2. Validate with `ast.parse()` before writing to disk
3. Write to `tests/test_blast_radius.py` in the target repo
4. Run `pytest -v --tb=short --json-report`
5. Parse and return `{passed, failed, returncode, stdout}`

---

## Phase 7 — Interaction Data Loop (`telemetry.py`)
**Goal:** Every run writes a training record.
**Time estimate: 45 min**

```sql
CREATE TABLE interactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT    NOT NULL,
    repo_path   TEXT    NOT NULL,
    ground_zero TEXT    NOT NULL,   -- JSON: list of changed function names
    prompt      TEXT    NOT NULL,
    generated   TEXT    NOT NULL,
    passed      INTEGER,
    failed      INTEGER,
    edited      INTEGER DEFAULT 0,
    final_code  TEXT
);
```

Each row is a complete supervised fine-tuning example:
- **Input:** `prompt`
- **Output:** `final_code` if `edited=1`, else `generated`
- **Quality signal:** `passed / (passed + failed)`

---

## Phase 8 — CLI Entrypoint (`__main__.py`)
**Goal:** Two commands wire the full tool.
**Time estimate: 30 min**

```bash
# Build / rebuild the SQLite call graph for a repo
python -m blast_radius index /path/to/repo

# Run the full pipeline on uncommitted changes
python -m blast_radius analyze /path/to/repo
python -m blast_radius analyze /path/to/repo --dry-run
```

```python
app = typer.Typer()

@app.command()
def index(repo: str):
    """Parse repo and build the SQLite call graph."""
    ...

@app.command()
def analyze(
    repo: str,
    dry_run: bool = typer.Option(False),
):
    """git diff → blast radius → synthesize tests → run tests."""
    ...
```

---

## Build Order

1. `pyproject.toml` + venv + smoke test
2. `parser/tree_sitter_manager.py` — adapt from CGC
3. `parser/python.py` — adapt PY_QUERIES + extraction logic from CGC
4. `parser/__init__.py` — TreeSitterParser dispatcher
5. `indexer.py` — two-pass repo walker + SQLite writer
6. `graph.py` — SQLite client + recursive CTE queries
7. `git_diff.py` — diff parser
8. `resolver.py` — line→FunctionNode lookup + fuzzy fallback
9. `synthesizer.py` — context builder + Gemini call
10. `runner.py` — write test file + pytest execution
11. `telemetry.py` — SQLite interaction logger
12. `__main__.py` — wire `index` and `analyze` commands
13. End-to-end: index a real repo → make a change → run analyze → inspect `data/`

---

## Known Risks & Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| `tree-sitter-language-pack` v1.x breaks parser API | High (already hit) | Pin to `==0.6.0` |
| Call resolution misses cross-module calls | Medium | Pass 2 resolves by name; log unresolved calls — they indicate the index missed a file |
| Index is stale when diff runs | High | Re-run `blast-radius index` before analyzing; add staleness warning if graph mtime < repo mtime |
| Gemini returns markdown fences | Medium | Strip fences; validate with `ast.parse()` before writing |
| Large repos overflow Gemini context | Medium | Cap affected_functions to top 20 by cyclomatic_complexity |
| Fuzzy line matching gives wrong function | Low | Log uid + line range every time fuzzy fires; easy to audit in output |

---

## Future Extensions (Post-MVP)

- Multi-language support: JS/TS/Go parsers already in CGC's `languages/` — same adaptation pattern
- VS Code extension: show blast radius inline on save
- PR comment bot: post affected subgraph + tests as CI comment
- Fine-tuned model: distill `interactions.db` after 500+ runs
- KùzuDB backend: swap SQLite connection; recursive CTE logic is portable
