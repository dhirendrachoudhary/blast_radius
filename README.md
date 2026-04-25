# Blast Radius

**Blast Radius** is a self-contained developer tool that answers the question: *"I just changed this function — what else could break?"*

It indexes a Python codebase into a local SQLite call graph using tree-sitter, traverses that graph upstream from any changed function, then sends the subgraph to Gemini to automatically synthesize and run targeted pytest tests — all with zero external services.

---

## How It Works

```
blast-radius index /repo
    │
    ▼  tree-sitter parses every .py file
    Pass 1: functions + unresolved calls  →  SQLite
    Pass 2: resolve callee names → UIDs across all files

blast-radius analyze /repo
    │
    ▼  git diff --unified=0 HEAD
ChangedRange(file, lines)
    │
    ▼  SELECT * FROM functions WHERE line_start ≤ L ≤ line_end
FunctionNode  (ground zero)
    │
    ▼  recursive CTE: walk calls table backwards
BlastRadiusResult
   ├── affected_functions  (deduplicated upstream set)
   ├── entry_points        (callers with nothing above them)
   └── call_chains         (human-readable traversal paths)
    │
    ▼  Gemini: subgraph source + call chains → pytest code
Generated test file written + executed
    │
    ▼
interactions row logged to SQLite  (training data flywheel)
```

**No CGC. No FalkorDB. No daemon.** The parsing logic is adapted directly from [CodeGraphContext's source](https://github.com/Unix-Dev-Ops/Code-Graph-Context) and owned in this repo.

---

## Prerequisites

- Python 3.10+
- A Google Cloud project with Vertex AI enabled

---

## Setup

**1. Clone and create a virtual environment**

```bash
git clone https://github.com/dhirendrachoudhary/Seismic.git
cd Seismic
python -m venv .venv && source .venv/bin/activate
```

**2. Install dependencies**

```bash
pip install -e .
```

`tree-sitter-language-pack==0.6.0` is pinned in `pyproject.toml` — v1.x changes the module API and breaks parsing.

**3. Configure environment**

```bash
cp .env.example .env
# Set GEMINI_PROJECT and GEMINI_LOCATION
```

---

## Usage

```bash
# Step 1: build the SQLite call graph for a target repo (run once, re-run after large changes)
blast-radius index /path/to/your/repo

# Step 2: full pipeline on uncommitted changes
blast-radius analyze /path/to/your/repo

# Dry run: print blast radius without generating or running tests
blast-radius analyze /path/to/your/repo --dry-run
```

**Example output:**
```
Indexed 142 functions, 380 calls  →  data/blast_radius.db
Found 2 changed file(s)
Ground zero: ['calculate_discount', 'apply_coupon']
Blast radius: 7 function(s) affected
  process_order → apply_coupon → calculate_discount
  checkout → process_order → apply_coupon → calculate_discount
  api_checkout → checkout → process_order → apply_coupon
Tests: 5 passed, 1 failed
```

Generated tests are written to `tests/test_blast_radius.py` in the target repo.

---

## Project Structure

```
src/blast_radius/
├── parser/
│   ├── __init__.py             # TreeSitterParser dispatcher
│   ├── tree_sitter_manager.py  # thread-safe language/parser loader (adapted from CGC)
│   └── python.py               # PY_QUERIES + extraction logic (adapted from CGC)
├── indexer.py      # two-pass repo walker → SQLite graph writer
├── graph.py        # SQLite client + recursive CTE blast-radius queries
├── git_diff.py     # git diff parser → ChangedRange objects
├── resolver.py     # line ranges → FunctionNode via graph lookup
├── synthesizer.py  # Gemini prompt builder + test code generator
├── runner.py       # writes test file, runs pytest, captures results
├── telemetry.py    # SQLite interaction logger (training data flywheel)
└── __main__.py     # CLI entrypoint — index + analyze commands (typer)

docs/
├── build-plan.md   # full implementation plan with SQL queries + adapter notes
└── tasks.md        # phase-by-phase task tracker with checkboxes

data/
└── blast_radius.db # SQLite — call graph + telemetry (gitignored, created at runtime)
```

---

## Graph Schema (SQLite)

```sql
-- Call graph — written by indexer.py
functions(uid, name, file_path, line_start, line_end, source, complexity, decorators, docstring, repo_path)
calls(id, caller_uid, callee_name, callee_uid, line_number)

-- Training data — written by telemetry.py
interactions(id, timestamp, repo_path, ground_zero, prompt, generated, passed, failed, edited, final_code)
```

`uid` format: `"{file_path}::{name}::{line_start}"`

The blast-radius traversal is a single SQLite recursive CTE — equivalent to Cypher `[:CALLS*1..10]` but with no external service required.

---

## Interaction Logging

Every pipeline run is logged to `data/blast_radius.db`:

| Column | Purpose |
|---|---|
| `prompt` | Full subgraph context sent to Gemini |
| `generated` | Raw Gemini output |
| `passed` / `failed` | pytest result counts |
| `edited` | 1 if a developer manually corrected the output |
| `final_code` | Post-edit code — the ground-truth fine-tuning label |

After ~500 runs, this dataset is sufficient to fine-tune a smaller model on the codebase's specific fixture conventions and mock patterns.

---

## Environment Variables

| Variable | Description |
|---|---|
| `GEMINI_PROJECT` | GCP project ID for Vertex AI |
| `GEMINI_LOCATION` | GCP region (e.g. `us-central1`) |
| `BLAST_RADIUS_DB` | Path to SQLite DB (default: `data/blast_radius.db`) |

---

## Development Roadmap

- [x] Project scaffold, documentation, and task tracker
- [ ] Phase 2: Parser — adapt tree-sitter manager + Python query logic from CGC
- [ ] Phase 3: Indexer — two-pass repo walker writing to SQLite
- [ ] Phase 4: Graph client — SQLite recursive CTE queries
- [ ] Phase 5: Git diff resolver
- [ ] Phase 6: Test synthesizer + runner (Gemini + pytest)
- [ ] Phase 7: Telemetry loop
- [ ] Phase 8: CLI entrypoint (`index` + `analyze` commands)
- [ ] Phase 9: End-to-end validation on a real repo
- [ ] Multi-language support (JS/TS/Go — same adapter pattern)
- [ ] VS Code extension
- [ ] CI/CD PR comment bot
- [ ] Fine-tuned model distillation

See [`docs/tasks.md`](docs/tasks.md) for the detailed task tracker.
