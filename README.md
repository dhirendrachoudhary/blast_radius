# Blast Radius

> **Impact analysis for Python code changes.** Know exactly what could break before you ship.

[![PyPI](https://img.shields.io/pypi/v/blast-radius)](https://pypi.org/project/blast-radius/)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

---

## What It Does

Blast Radius answers the question every developer faces after making a change:

> **"What else could break?"**

It parses your Python codebase into a local call graph, finds every function upstream of your change, then generates targeted pytest tests against the affected entry points — all without leaving the terminal.

```
$ blast-radius index /path/to/repo
Indexed 312 functions, 847 calls → data/blast_radius.db

$ blast-radius analyze /path/to/repo
Ground zero: ['calculate_discount']
Blast radius: 5 function(s)
Call chains:
  api_checkout → checkout → process_order → calculate_discount
  checkout → process_order → calculate_discount
```

**No graph databases. No daemons. No config servers.** Everything runs in a single SQLite file.

---

## Installation

```bash
pip install blast-radius
```

> `tree-sitter-language-pack==0.6.0` is pinned as a dependency. Version 1.x introduces breaking API changes.

---

## Quick Start

**1. Index your repository** (run once; re-run after large refactors)

```bash
blast-radius index /path/to/your/repo
```

**2. Make changes, then analyze**

```bash
# After editing code (staged or unstaged changes work)
blast-radius analyze /path/to/your/repo

# Preview blast radius without generating tests
blast-radius analyze /path/to/your/repo --dry-run
```

---

## Configuration

Copy `.env.example` to `.env`:

```env
GEMINI_PROJECT=your-gcp-project-id
GEMINI_LOCATION=us-central1
BLAST_RADIUS_DB=data/blast_radius.db   # optional, this is the default
```

| Variable | Description | Required |
|---|---|---|
| `GEMINI_PROJECT` | GCP project ID for Vertex AI | For test generation |
| `GEMINI_LOCATION` | GCP region | For test generation |
| `BLAST_RADIUS_DB` | SQLite database path | No (default: `data/blast_radius.db`) |

---

## How It Works

### Index phase

Tree-sitter parses every `.py` file into a SQLite call graph in two passes:

- **Pass 1** — extract functions (name, line range, source, complexity) and unresolved calls
- **Pass 2** — resolve `callee_name → callee_uid` across all files

### Analyze phase

```
git diff HEAD
    ↓
ChangedRange(file, lines)
    ↓  SELECT * FROM functions WHERE line_start ≤ L ≤ line_end
FunctionNode  (ground zero)
    ↓  recursive CTE: walk calls table backwards
BlastRadiusResult
   ├── affected_functions
   ├── entry_points (no callers above them)
   └── call_chains
    ↓
Gemini → pytest tests → run → log to SQLite
```

The blast-radius traversal is a single SQLite recursive CTE — no external graph database needed:

```sql
WITH RECURSIVE blast_radius(uid, depth) AS (
    SELECT uid, 0 FROM functions WHERE name IN ($changed)
    UNION ALL
    SELECT c.caller_uid, br.depth + 1
    FROM calls c JOIN blast_radius br ON br.uid = c.callee_uid
    WHERE br.depth < 10
)
SELECT DISTINCT f.* FROM functions f JOIN blast_radius br ON br.uid = f.uid;
```

---

## Database Schema

```sql
functions(uid, name, file_path, line_start, line_end, source, complexity, decorators, docstring, repo_path)
calls(id, caller_uid, callee_name, callee_uid, line_number)
interactions(id, timestamp, repo_path, ground_zero, prompt, generated, passed, failed, edited, final_code)
```

`uid` format: `"{file_path}::{name}::{line_start}"`

---

## Project Structure

```
src/blast_radius/
├── parser/
│   ├── tree_sitter_manager.py   # thread-safe language/parser loader
│   └── python.py                # tree-sitter queries + AST extraction
├── indexer.py      # two-pass repo walker
├── graph.py        # SQLite client + recursive CTE queries
├── git_diff.py     # git diff → ChangedRange
├── resolver.py     # line ranges → FunctionNode
├── synthesizer.py  # Gemini context builder + test synthesis
├── runner.py       # write test file + execute pytest
├── telemetry.py    # SQLite interaction logger
└── __main__.py     # CLI (typer)
```

---

## Development Roadmap

- [x] Parser — tree-sitter AST extraction (functions, calls, complexity)
- [x] Indexer — two-pass SQLite call graph builder
- [x] Graph — recursive CTE blast-radius queries
- [x] Git diff resolver — changed lines → ground-zero functions
- [ ] Test synthesizer — Gemini context + pytest generation (Phase 6)
- [ ] Telemetry — interaction logging for fine-tuning (Phase 7)
- [ ] Multi-language support (JS/TS/Go)
- [ ] VS Code extension
- [ ] CI/CD PR comment bot

---

## Testing

```bash
pytest tests/ -v
```

---

## License

MIT — see [LICENSE](LICENSE).

---

## Acknowledgments

Parser logic adapted from [CodeGraphContext](https://github.com/Unix-Dev-Ops/Code-Graph-Context).
