# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Blast Radius Code Analyzer** — a self-contained CLI tool that:
1. Indexes a Python repo into a local SQLite call graph using tree-sitter (no external services)
2. Parses `git diff` to find changed functions
3. Traverses the call graph (SQLite recursive CTE) to find all upstream callers
4. Sends the subgraph to Gemini to synthesize targeted pytest tests
5. Runs those tests and logs every interaction to SQLite for future fine-tuning

**No CGC. No FalkorDB. No daemon processes.** The parser logic is adapted directly from the [CGC source](https://github.com/Unix-Dev-Ops/Code-Graph-Context) but owned in this repo.

## Environment Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env   # fill in GEMINI_PROJECT
```

Smoke test that tree-sitter is working:
```python
from tree_sitter_language_pack import get_parser
tree = get_parser("python").parse(b"def hello(): pass")
print(tree.root_node.sexp())
```

## Running the Tool

```bash
# Step 1: build the SQLite call graph for a repo
python -m blast_radius index /path/to/target/repo

# Step 2: run the full pipeline on uncommitted changes
python -m blast_radius analyze /path/to/target/repo

# Dry run: print blast radius without generating/running tests
python -m blast_radius analyze /path/to/target/repo --dry-run
```

## Running Tests

```bash
pytest tests/ -v
pytest tests/test_graph.py -v        # graph client only
pytest tests/ -v --tb=short
```

## Project Structure

```
src/blast_radius/
├── parser/
│   ├── __init__.py             # TreeSitterParser dispatcher
│   ├── tree_sitter_manager.py  # thread-safe language/parser loader (adapted from CGC)
│   └── python.py               # PY_QUERIES + _find_functions + _find_calls (adapted from CGC)
├── indexer.py      # walks repo → parser → writes SQLite graph (two-pass)
├── graph.py        # SQLite client + recursive CTE blast-radius queries
├── git_diff.py     # subprocess git diff → ChangedRange objects
├── resolver.py     # ChangedRange → FunctionNode via graph lookup
├── synthesizer.py  # Gemini prompt builder + test code generator
├── runner.py       # writes test file, executes pytest, captures results
├── telemetry.py    # SQLite interaction logger (training data flywheel)
└── __main__.py     # CLI entrypoint — index + analyze commands (typer)

data/blast_radius.db   # SQLite — graph + telemetry (gitignored, created at runtime)
docs/build-plan.md     # detailed implementation plan with SQL queries
docs/tasks.md          # phase-by-phase task tracker
```

## Architecture

### Data Flow

```
python -m blast_radius index /repo
    │
    ▼  rglob("*.py") → tree-sitter parse
    Pass 1: functions + unresolved calls → SQLite
    Pass 2: resolve callee_name → callee_uid across all files
    └── data/blast_radius.db (functions + calls tables)

python -m blast_radius analyze /repo
    │
    ▼  git diff --unified=0 HEAD
ChangedRange(file_path, [line_numbers])          ← git_diff.py
    │
    ▼  SELECT * FROM functions WHERE file_path=? AND line_start<=? AND line_end>=?
FunctionNode  (ground zero)                      ← resolver.py
    │
    ▼  recursive CTE: walk calls backwards
BlastRadiusResult                                ← graph.py
   ├── affected_functions  (deduplicated)
   ├── entry_points        (no callers above them)
   └── call_chains         (path strings)
    │
    ▼  build_context() → Gemini → test code
pytest file written + executed                   ← synthesizer.py + runner.py
    │
    ▼
interactions row (prompt, output, pass/fail)     ← telemetry.py
```

### SQLite Schema

```sql
-- Call graph (written by indexer.py)
functions(uid, name, file_path, line_start, line_end, source, complexity, decorators, docstring, repo_path)
calls(id, caller_uid, callee_name, callee_uid, line_number)

-- Training data (written by telemetry.py)
interactions(id, timestamp, repo_path, ground_zero, prompt, generated, passed, failed, edited, final_code)
```

`uid` format: `"{file_path}::{name}::{line_start}"`

### Blast Radius Query

```sql
WITH RECURSIVE blast_radius(uid, depth) AS (
    SELECT uid, 0 FROM functions WHERE name IN ($changed_names)
    UNION ALL
    SELECT c.caller_uid, br.depth + 1
    FROM calls c JOIN blast_radius br ON br.uid = c.callee_uid
    WHERE br.depth < 10
)
SELECT DISTINCT f.* FROM functions f JOIN blast_radius br ON br.uid = f.uid;
```

### Parser (adapted from CGC)

`parser/python.py` contains `PY_QUERIES` — tree-sitter query strings for:
- `function_definition` nodes: name, params, decorators, body
- `call` nodes: identifier and attribute calls
- `import_statement` / `import_from_statement`

`_find_functions()` extracts: name, line_start, line_end, source slice, cyclomatic complexity, decorators, docstring.  
`_find_calls()` extracts: caller_uid (by line range lookup), callee_name, line_number.

## Known Pitfalls

| Issue | Fix |
|---|---|
| `tree-sitter-language-pack` v1.x breaks parser API | Pinned to `==0.6.0` in pyproject.toml — do not upgrade without testing |
| Index stale when diff runs | Re-run `blast-radius index` before analyzing; staleness warning fires if graph mtime < repo mtime |
| Unresolved calls after pass 2 | Expected for stdlib/third-party calls — logged but not a failure |
| Gemini returns markdown fences | Strip before writing; validate with `ast.parse()` |
| Large repos overflow Gemini context | Cap `affected_functions` to top 20 by `complexity` in `build_context()` |
| Fuzzy line match fires unexpectedly | Logged with uid + line range — check if index is stale |
