# Task Tracker — Blast Radius Code Analyzer

> Status legend: `[ ]` Not started · `[~]` In progress · `[x]` Done · `[!]` Blocked
> Stack: tree-sitter-language-pack + SQLite + Gemini. No CGC. No FalkorDB.

---

## Phase 1 — Environment Setup
**Goal:** Clean Python environment with tree-sitter confirmed working.
**Estimate:** 20 min

- [x] **1.1** Create `pyproject.toml` with pinned dependencies (no falkordb, no codegraphcontext)
- [x] **1.2** Create `.env.example` with GEMINI_PROJECT, GEMINI_LOCATION, BLAST_RADIUS_DB
- [ ] **1.3** Confirm `tree-sitter-language-pack==0.6.0` + `tree-sitter>=0.21.0` install cleanly
- [ ] **1.4** Run smoke test: parse a Python snippet with tree-sitter, print AST

---

## Phase 2 — Parser (adapted from CGC source)
**Goal:** tree-sitter → `ParsedFile` with functions and call edges. No DB dependency.
**Estimate:** 2 hours
**Files:** `src/blast_radius/parser/`

- [ ] **2.1** Adapt `tree_sitter_manager.py` from CGC (`utils/tree_sitter_manager.py`)
  - [ ] Strip CGC-specific imports
  - [ ] Keep: language caching, thread-safe `create_parser()`, `execute_query()` shim
- [ ] **2.2** Adapt `python.py` from CGC (`tools/languages/python.py`)
  - [ ] Keep: `PY_QUERIES` dict (function_definition, calls, imports, lambda queries)
  - [ ] Keep: `_find_functions()` — name, line numbers, source, complexity, decorators, docstring
  - [ ] Keep: `_find_calls()` — caller_uid, callee_name, line_number
  - [ ] Keep: `pre_scan_python()` — name→filepath map for cross-file resolution
  - [ ] Strip: all Neo4j / FalkorDB / database code
- [ ] **2.3** Write `parser/__init__.py` — `TreeSitterParser` dispatcher (adapted from CGC `graph_builder.py`)
- [ ] **2.4** Define `FunctionNode` and `CallEdge` and `ParsedFile` dataclasses
- [ ] **2.5** Unit test: parse a real `.py` file, assert functions + calls extracted correctly

---

## Phase 3 — Indexer
**Goal:** Walk repo → parse every `.py` → write call graph to SQLite.
**Estimate:** 1.5 hours
**File:** `src/blast_radius/indexer.py`

- [ ] **3.1** Create SQLite schema: `functions` table + `calls` table + indexes
- [ ] **3.2** Implement Pass 1 — parse all `.py` files, insert functions + unresolved calls
  - [ ] Skip dirs: `.venv`, `venv`, `__pycache__`, `.git`, `node_modules`, `dist`, `build`
  - [ ] Generate `uid` as `"{file_path}::{name}::{line_start}"`
- [ ] **3.3** Implement Pass 2 — resolve `callee_name` → `callee_uid` across all files
  - [ ] Log count of unresolved calls after pass 2 (indicates cross-package calls, not a bug)
- [ ] **3.4** Staleness check: warn if graph mtime < repo newest file mtime
- [ ] **3.5** Test: index a real Python repo, assert function count and call count are non-zero

---

## Phase 4 — Graph Client
**Goal:** SQLite wrapper with all blast-radius queries (replaces FalkorDB).
**Estimate:** 1 hour
**File:** `src/blast_radius/graph.py`

- [ ] **4.1** Implement `FunctionNode` and `BlastRadiusResult` dataclasses
- [ ] **4.2** Implement `CodeGraph.__init__` — connect to SQLite at `BLAST_RADIUS_DB`
- [ ] **4.3** Implement `find_by_name(name: str) -> FunctionNode | None`
- [ ] **4.4** Implement `find_by_line(file_path: str, line: int) -> FunctionNode | None`
- [ ] **4.5** Implement `get_blast_radius(fn_names: list[str]) -> BlastRadiusResult`
  - [ ] Recursive CTE for affected function set
  - [ ] Entry points: blast radius set minus any uid appearing as callee
  - [ ] Call chain accumulation via recursive CTE with path string
- [ ] **4.6** Unit tests: mock SQLite data, assert blast radius traversal is correct

---

## Phase 5 — Git Diff Resolver
**Goal:** `git diff` → `ChangedRange` objects → `FunctionNode` objects.
**Estimate:** 1.5 hours
**Files:** `src/blast_radius/git_diff.py`, `src/blast_radius/resolver.py`

- [ ] **5.1** Implement `ChangedRange` dataclass
- [ ] **5.2** Implement `get_changed_ranges(repo_path: str) -> list[ChangedRange]`
  - [ ] Parse `@@ -a,b +c,d @@` hunk headers
  - [ ] Expand ranges to individual line numbers
  - [ ] Handle new files (no `-` side in hunk)
  - [ ] Handle renamed files via `--find-renames`
- [ ] **5.3** Implement `resolve_to_functions(changed_ranges, graph) -> list[FunctionNode]`
  - [ ] Deduplicate lines before querying
  - [ ] Fuzzy fallback: widen to ±3 lines if exact match returns nothing
  - [ ] Log when fuzzy matching fires (stale index signal)
- [ ] **5.4** Test with a real staged change in a known Python repo

---

## Phase 6 — Agentic Test Synthesizer
**Goal:** `BlastRadiusResult` → Gemini → runnable pytest code → executed.
**Estimate:** 2 hours
**Files:** `src/blast_radius/synthesizer.py`, `src/blast_radius/runner.py`

- [ ] **6.1** Implement `build_context(result: BlastRadiusResult) -> str`
  - [ ] Ground zero section (with file:line reference)
  - [ ] Affected functions section
  - [ ] Entry points section
  - [ ] Call chains section
  - [ ] Cap affected_functions to top 20 by `complexity` before building
- [ ] **6.2** Write `SYSTEM_PROMPT` — pytest-only, mock I/O, happy + failure path per entry point
- [ ] **6.3** Implement `synthesize_tests(context: str) -> str` via Vertex AI Gemini
- [ ] **6.4** Strip markdown fences from Gemini output
- [ ] **6.5** Validate with `ast.parse()` before writing to disk; raise on invalid syntax
- [ ] **6.6** Implement `write_and_run(test_code: str, repo_path: str) -> dict`
  - [ ] Write to `tests/test_blast_radius.py` in target repo
  - [ ] Run `pytest -v --tb=short --json-report`
  - [ ] Parse and return `{passed, failed, returncode, stdout, test_file}`
- [ ] **6.7** Test synthesizer with a hardcoded `BlastRadiusResult` to verify Gemini output quality

---

## Phase 7 — Interaction Data Loop
**Goal:** Every pipeline run writes a training record to SQLite.
**Estimate:** 45 min
**File:** `src/blast_radius/telemetry.py`

- [ ] **7.1** Define `interactions` table schema (in same DB as graph, or separate — decide)
- [ ] **7.2** Implement `TelemetryLogger.__init__` with auto schema creation
- [ ] **7.3** Implement `TelemetryLogger.log(repo_path, ground_zero, prompt, generated, run_result)`
- [ ] **7.4** Implement `TelemetryLogger.mark_edited(interaction_id, final_code)`
- [ ] **7.5** Unit test: schema creation + round-trip log + mark_edited

---

## Phase 8 — CLI Entrypoint
**Goal:** Two commands — `index` and `analyze` — wire the full pipeline.
**Estimate:** 30 min
**File:** `src/blast_radius/__main__.py`

- [ ] **8.1** Implement `index` command — calls `indexer.run(repo_path)`
- [ ] **8.2** Implement `analyze` command — full pipeline: diff → resolve → blast radius → synthesize → run → log
- [ ] **8.3** Implement `--dry-run` flag — stop after printing blast radius
- [ ] **8.4** Startup validation: check GEMINI_PROJECT env var set, DB file exists (for analyze)
- [ ] **8.5** Clear error message if DB not found: "Run `blast-radius index <repo>` first"

---

## Phase 9 — End-to-End Validation
**Goal:** Full pipeline works on a real codebase.
**Estimate:** 1 hour

- [ ] **9.1** Index a real Python repo (`blast-radius index /path/to/repo`)
- [ ] **9.2** Make a real function change in that repo
- [ ] **9.3** Run `blast-radius analyze /path/to/repo`
- [ ] **9.4** Verify blast radius output looks correct (right functions affected)
- [ ] **9.5** Verify generated tests are syntactically valid
- [ ] **9.6** Verify `data/interactions.db` (or chosen path) has a new row
- [ ] **9.7** Write `scripts/demo.sh` as the canonical e2e demo runner

---

## Backlog — Post-MVP

- [ ] Multi-language support: JS/TS/Go — same adapter pattern as `parser/python.py`
- [ ] VS Code extension: show blast radius inline on file save
- [ ] CI/CD PR comment bot: post affected subgraph + generated tests on each PR
- [ ] Fine-tuned model: distill `interactions.db` into a smaller local model after 500+ runs
- [ ] Incremental re-index: only re-parse files changed since last `mtime`
- [ ] KùzuDB backend option: swap SQLite connection, keep same query logic
