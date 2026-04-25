# Blast Radius

> **Impact analysis for Python code changes.** Automatically determine what functions could be affected by your changes, then generate and run targeted tests to verify correctness.

[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/downloads/)
[![License](https://img.shields.io/badge/License-MIT-green)](#license)
[![Phase](https://img.shields.io/badge/Phase-2%2F9%20Complete-orange)](#development-roadmap)

---

## Overview

Blast Radius answers a critical question every developer faces:

> **"I just changed this function — what else could break?"**

Rather than manually tracing through your codebase or running your entire test suite, Blast Radius:

1. **Analyzes** your Python repository using tree-sitter to build a precise call graph
2. **Identifies** the impact radius of your code changes (which functions are affected upstream)
3. **Generates** targeted pytest tests for the affected functions using Claude/Gemini
4. **Validates** your changes by running the generated tests
5. **Logs** every interaction for future model fine-tuning

**Everything runs locally.** No external services (except for Gemini), no daemon processes, no database servers to manage.

---

## Key Features

- 🔍 **Precise Call Graph Analysis** — Uses tree-sitter to parse Python code into a queryable SQLite call graph
- ⚡ **Fast Impact Determination** — O(n) recursive CTE queries to find all affected functions
- 🧪 **Automated Test Generation** — Claude/Gemini synthesizes targeted pytest tests based on the affected subgraph
- 💾 **Self-Contained** — Single SQLite database, no external graph databases or microservices
- 🎯 **Developer-Focused** — Works on uncommitted changes via `git diff`
- 📊 **Training Data Flywheel** — Logs every interaction for fine-tuning smaller models
- 🔒 **Offline-First** — Parser and graph traversal work completely offline; Gemini calls are optional

---

## Quick Start

### Prerequisites

- **Python 3.10+**
- **Google Cloud project** with Vertex AI enabled (for test generation)
- **Git** repository (to track changes)

### Installation

**1. Clone the repository**

```bash
git clone https://github.com/dhirendrachoudhary/blast_radius.git
cd blast_radius
```

**2. Create a virtual environment**

```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
```

**3. Install dependencies**

```bash
pip install -e .
```

> **Note:** `tree-sitter-language-pack==0.6.0` is pinned in `pyproject.toml`. Version 1.x introduces breaking API changes. Do not upgrade without testing.

**4. Configure Google Cloud credentials**

```bash
cp .env.example .env
# Edit .env and set:
# - GEMINI_PROJECT: Your GCP project ID
# - GEMINI_LOCATION: GCP region (e.g., us-central1)
```

---

## Usage

### Indexing a Repository

Build a SQLite call graph for any Python codebase:

```bash
blast-radius index /path/to/your/repo
```

This produces `data/blast_radius.db` containing:
- All functions with their source code, line ranges, and cyclomatic complexity
- All function calls, including caller/callee relationships
- Ready for blast-radius analysis

> Run this once per repo, then re-run after major refactors to keep the index fresh.

### Analyzing Changes

Determine the blast radius of your uncommitted changes:

```bash
# Make changes to your repo
git add src/your_module.py
git commit -m "refactor: optimize calculation logic"

# Analyze and generate tests
blast-radius analyze /path/to/your/repo
```

**Output:**
```
Indexed 142 functions, 380 calls  →  data/blast_radius.db
Found 2 changed file(s)
Ground zero: ['calculate_discount', 'apply_coupon']
Blast radius: 7 function(s) affected
  
Call chains:
  process_order → apply_coupon → calculate_discount
  checkout → process_order → apply_coupon → calculate_discount
  api_checkout → checkout → process_order → apply_coupon

Generated tests:
  tests/test_blast_radius.py
  
Test results:
  ✓ test_calculate_discount_with_valid_input (0.23s)
  ✓ test_calculate_discount_edge_case (0.18s)
  ✓ test_apply_coupon_happy_path (0.31s)
  ✓ test_apply_coupon_error_handling (0.22s)
  ✓ test_checkout_full_flow (0.54s)
  ✗ test_api_checkout_edge_case (expected 150, got 140)

5 passed, 1 failed
```

### Dry Run Mode

Preview the blast radius without generating or running tests:

```bash
blast-radius analyze /path/to/your/repo --dry-run
```

---

## Architecture

### Data Flow

```
┌─ Index Phase ──────────────────────────────────────────┐
│                                                         │
│  $ blast-radius index /repo                           │
│    ↓                                                   │
│    tree-sitter parses every .py file                  │
│    ↓                                                   │
│    Pass 1: extract functions + unresolved calls       │
│    Pass 2: resolve callee_name → uid across files     │
│    ↓                                                   │
│    data/blast_radius.db                               │
│    ├─ functions table                                 │
│    └─ calls table                                     │
│                                                        │
└────────────────────────────────────────────────────────┘

┌─ Analyze Phase ────────────────────────────────────────┐
│                                                         │
│  $ blast-radius analyze /repo                         │
│    ↓                                                   │
│    git diff HEAD → ChangedRange                       │
│    ↓                                                   │
│    Query: SELECT functions WHERE line_range matches   │
│    ↓                                                   │
│    FunctionNode (ground zero)                         │
│    ↓                                                   │
│    Recursive CTE: walk calls backward                 │
│    ↓                                                   │
│    BlastRadiusResult                                  │
│    ├─ affected_functions (all upstream)               │
│    ├─ entry_points (no callers above)                 │
│    └─ call_chains (human-readable paths)              │
│    ↓                                                   │
│    Gemini: generate pytest tests                      │
│    ↓                                                   │
│    Run tests + log results                            │
│    ↓                                                   │
│    interactions table (training data)                 │
│                                                        │
└────────────────────────────────────────────────────────┘
```

### Database Schema

**Call Graph (SQLite)**

```sql
-- Functions extracted from source code
functions(
  uid TEXT PRIMARY KEY,        -- "{file_path}::{name}::{line_start}"
  name TEXT,
  file_path TEXT,
  line_start INTEGER,
  line_end INTEGER,
  source TEXT,                 -- Full function source code
  complexity INTEGER,          -- Cyclomatic complexity
  decorators TEXT,             -- JSON list
  docstring TEXT,
  repo_path TEXT
)

-- Function calls (edges in the call graph)
calls(
  id INTEGER PRIMARY KEY,
  caller_uid TEXT REFERENCES functions(uid),
  callee_name TEXT,
  callee_uid TEXT REFERENCES functions(uid),  -- NULL until Phase 3 resolution
  line_number INTEGER
)
```

**Training Data (SQLite)**

```sql
interactions(
  id INTEGER PRIMARY KEY,
  timestamp TEXT,
  repo_path TEXT,
  ground_zero TEXT,           -- JSON: changed function names
  prompt TEXT,                -- Subgraph sent to Gemini
  generated TEXT,             -- Gemini output
  passed INTEGER,
  failed INTEGER,
  edited INTEGER DEFAULT 0,   -- 1 if developer corrected output
  final_code TEXT             -- Post-edit code (ground truth)
)
```

---

## Project Structure

```
blast_radius/
├── src/blast_radius/
│   ├── parser/
│   │   ├── __init__.py              # TreeSitterParser dispatcher
│   │   ├── tree_sitter_manager.py   # Thread-safe language/parser loader
│   │   └── python.py                # Python AST queries + extraction
│   ├── indexer.py                   # Two-pass repo walker → SQLite
│   ├── graph.py                     # SQLite queries (recursive CTE)
│   ├── git_diff.py                  # Parse git diff → line ranges
│   ├── resolver.py                  # Map line ranges → functions
│   ├── synthesizer.py               # Build context + Gemini prompts
│   ├── runner.py                    # Execute pytest + capture results
│   ├── telemetry.py                 # Log interactions to SQLite
│   └── __main__.py                  # CLI (typer)
│
├── tests/
│   ├── test_smoke.py               # Tree-sitter API validation (10 tests)
│   └── test_parser.py              # Parser extraction tests (20 tests)
│
├── data/
│   └── blast_radius.db             # SQLite graph (gitignored, runtime)
│
├── pyproject.toml                  # Dependencies + build config
├── .env.example                    # Environment variable template
└── README.md                       # This file
```

---

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GEMINI_PROJECT` | Google Cloud project ID | Yes (for test generation) |
| `GEMINI_LOCATION` | GCP region (e.g., `us-central1`) | Yes (for test generation) |
| `BLAST_RADIUS_DB` | Path to SQLite database | No (default: `data/blast_radius.db`) |

Create a `.env` file:

```bash
cp .env.example .env
# Edit .env with your GCP credentials
```

---

## Development Roadmap

| Phase | Task | Status |
|-------|------|--------|
| 1 | Environment setup + tree-sitter validation | ✅ Complete |
| 2 | Parser wrapper (tree-sitter → dataclasses) | ✅ Complete |
| 3 | Two-pass indexer (repo → SQLite) | ⏳ In Progress |
| 4 | Graph client (SQLite recursive CTE) | ⏳ Planned |
| 5 | Git diff resolver | ⏳ Planned |
| 6 | Test synthesizer + runner (Gemini) | ⏳ Planned |
| 7 | Telemetry loop (training data) | ⏳ Planned |
| 8 | CLI entrypoint (typer) | ⏳ Planned |
| 9 | End-to-end validation | ⏳ Planned |
| — | Multi-language support (JS/TS/Go) | 📋 Future |
| — | VS Code extension | 📋 Future |
| — | PR comment bot | 📋 Future |
| — | Model distillation | 📋 Future |

---

## Testing

Run the test suite:

```bash
# All tests
pytest tests/ -v

# Specific test module
pytest tests/test_parser.py -v

# With coverage
pytest tests/ --cov=src/blast_radius
```

**Current Status:** 30/30 tests passing ✅

- `test_smoke.py` (10 tests) — Tree-sitter API validation
- `test_parser.py` (20 tests) — Parser extraction and dataclass correctness

---

## How It Works (Technical Deep Dive)

### Phase 1: Parsing

Tree-sitter parses Python source code into an Abstract Syntax Tree (AST). We extract:

- **Functions** — name, line range, source code, complexity, decorators, docstring
- **Calls** — caller, callee, line number
- **uid** — unique identifier: `"{file_path}::{name}::{line_start}"`

See `src/blast_radius/parser/python.py` for implementation details.

### Phase 2: Indexing

A two-pass algorithm builds the call graph:

**Pass 1:** Parse all `.py` files, insert functions + unresolved calls into SQLite.

**Pass 2:** Resolve `callee_name` → `callee_uid` using a pre-scan map.

Why two passes? Functions may be called before they're defined (across files).

### Phase 3: Blast Radius Query

Find all upstream callers using a **recursive CTE** (Common Table Expression):

```sql
WITH RECURSIVE blast_radius(uid, depth) AS (
    -- Anchor: start with changed functions
    SELECT uid, 0 FROM functions WHERE name IN ($changed_names)
    
    UNION ALL
    
    -- Recursive: find all callers
    SELECT c.caller_uid, br.depth + 1
    FROM calls c
    JOIN blast_radius br ON br.uid = c.callee_uid
    WHERE br.depth < 10  -- Prevent infinite loops
)
SELECT DISTINCT f.* FROM functions f
JOIN blast_radius br ON br.uid = f.uid;
```

This is equivalent to Neo4j's `[:CALLS*1..10]` pattern but requires **zero external services**.

### Phase 4: Test Generation

The subgraph (all affected functions + call chains) is sent to Claude/Gemini:

```
System Prompt:
"You are a senior Python test engineer. Here are:
- Functions that changed (Ground Zero)
- All functions impacted by those changes (Blast Radius)
- API entry points (callers with no callers)
- Call chains showing how they're connected

Write pytest tests targeting the entry points. Mock all I/O."

User Prompt:
[Full subgraph source code + call chains]

Output:
pytest code
```

---

## Known Limitations

| Issue | Workaround |
|-------|-----------|
| **Index staleness** | Re-run `blast-radius index` after large refactors |
| **Large repos exceed Gemini context** | Limit affected functions to top 20 by complexity |
| **Unresolved cross-package calls** | Expected for stdlib/third-party; logged but not critical |
| **Multi-file call resolution** | Resolved by uid; ambiguous names get first match |

---

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Write tests for your changes
4. Ensure all tests pass (`pytest tests/ -v`)
5. Commit with clear messages (`git commit -m "feat: add..."`)
6. Push and open a pull request

---

## License

MIT License — see LICENSE file for details.

---

## Acknowledgments

Parser logic adapted from [CodeGraphContext](https://github.com/Unix-Dev-Ops/Code-Graph-Context) by Unix-Dev-Ops. We own and maintain our fork for self-contained operation.

---

## Support & Feedback

- **Report bugs** — Open an issue on GitHub
- **Ask questions** — Start a discussion
- **Suggest features** — Open a feature request

---

## Roadmap

### Short Term (Next 3 Months)

- [ ] Complete Phase 3-9 implementation
- [ ] End-to-end validation on public repos
- [ ] Performance optimization for large codebases

### Long Term (6-12 Months)

- [ ] Multi-language support (JavaScript, Go, TypeScript)
- [ ] VS Code extension for inline blast-radius display
- [ ] GitHub Actions bot for PR comments
- [ ] Fine-tuned models optimized for specific codebases

---

**Built with ❤️ for developers who care about code quality.**
