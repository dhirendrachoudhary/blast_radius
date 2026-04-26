"""Two-pass SQLite indexer for Python call graphs."""

import sqlite3
import json
from pathlib import Path
from typing import Optional
from dataclasses import dataclass

from .parser import TreeSitterParser
from .parser.python import FunctionNode, CallEdge


# ============================================================================
# Database Models
# ============================================================================


@dataclass
class Call:
    """Represents a row in the calls table."""

    id: int
    caller_uid: str
    callee_name: str
    callee_uid: Optional[str]
    line_number: int


# ============================================================================
# IndexerDB — SQLite Client
# ============================================================================


class IndexerDB:
    """SQLite client for call graph storage and queries."""

    def __init__(self, db_path: str):
        """
        Initialize database connection and create schema if needed.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        # Create parent directory if needed
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row  # Return rows as dicts
        self._init_schema()

    def _init_schema(self):
        """Create tables and indexes if they don't exist (idempotent)."""
        cursor = self.conn.cursor()

        # Create functions table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS functions (
                uid         TEXT PRIMARY KEY,
                name        TEXT NOT NULL,
                file_path   TEXT NOT NULL,
                line_start  INTEGER NOT NULL,
                line_end    INTEGER NOT NULL,
                source      TEXT,
                complexity  INTEGER DEFAULT 0,
                decorators  TEXT,
                docstring   TEXT,
                repo_path   TEXT NOT NULL
            )
            """
        )

        # Create calls table
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                caller_uid  TEXT NOT NULL REFERENCES functions(uid),
                callee_name TEXT NOT NULL,
                callee_uid  TEXT REFERENCES functions(uid),
                line_number INTEGER
            )
            """
        )

        # Create indexes
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_fn_name ON functions(name)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_fn_file_lines ON functions(file_path, line_start, line_end)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_calls_caller ON calls(caller_uid)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_calls_callee ON calls(callee_uid)"
        )

        self.conn.commit()

    def insert_functions(self, functions: list[FunctionNode], repo_path: str):
        """
        Batch insert functions. Uses UPSERT to handle re-indexing.

        Args:
            functions: List of FunctionNode objects to insert
            repo_path: Path to the repository (for tracking)
        """
        if not functions:
            return

        cursor = self.conn.cursor()

        # UPSERT: INSERT OR REPLACE (uid is PRIMARY KEY)
        cursor.executemany(
            """
            INSERT OR REPLACE INTO functions
            (uid, name, file_path, line_start, line_end, source, complexity, decorators, docstring, repo_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    fn.uid,
                    fn.name,
                    fn.file_path,
                    fn.line_start,
                    fn.line_end,
                    fn.source,
                    fn.complexity,
                    json.dumps(fn.decorators) if fn.decorators else None,
                    fn.docstring,
                    repo_path,
                )
                for fn in functions
            ],
        )

        self.conn.commit()

    def insert_calls_unresolved(self, calls: list[CallEdge]):
        """
        Batch insert calls with callee_uid = NULL (to be resolved in pass 2).

        Args:
            calls: List of CallEdge objects to insert
        """
        if not calls:
            return

        cursor = self.conn.cursor()

        cursor.executemany(
            """
            INSERT INTO calls (caller_uid, callee_name, callee_uid, line_number)
            VALUES (?, ?, NULL, ?)
            """,
            [(call.caller_uid, call.callee_name, call.line_number) for call in calls],
        )

        self.conn.commit()

    def get_unresolved_calls(self) -> list[Call]:
        """
        Fetch all calls where callee_uid IS NULL (not yet resolved).

        Returns:
            List of Call objects with unresolved callee_uid
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, caller_uid, callee_name, callee_uid, line_number FROM calls WHERE callee_uid IS NULL")

        return [
            Call(
                id=row["id"],
                caller_uid=row["caller_uid"],
                callee_name=row["callee_name"],
                callee_uid=row["callee_uid"],
                line_number=row["line_number"],
            )
            for row in cursor.fetchall()
        ]

    def find_function_uid_by_name(self, name: str) -> Optional[str]:
        """
        Look up a function by name. Returns first match (or None).

        Args:
            name: Function name to search for

        Returns:
            Function uid if found, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute("SELECT uid FROM functions WHERE name = ? LIMIT 1", (name,))

        row = cursor.fetchone()
        return row["uid"] if row else None

    def resolve_calls_batch(self, resolutions: list[tuple[str, int]]):
        """Batch-update callee_uid for multiple calls in a single transaction.

        Args:
            resolutions: list of (callee_uid, call_id) tuples
        """
        if not resolutions:
            return
        self.conn.executemany(
            "UPDATE calls SET callee_uid = ? WHERE id = ?", resolutions
        )
        self.conn.commit()

    def get_stats(self) -> dict:
        """
        Get indexing statistics.

        Returns:
            Dict with function count, call count, resolved/unresolved split
        """
        cursor = self.conn.cursor()

        cursor.execute("SELECT COUNT(*) as count FROM functions")
        fn_count = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM calls")
        call_count = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM calls WHERE callee_uid IS NOT NULL")
        resolved_count = cursor.fetchone()["count"]

        unresolved_count = call_count - resolved_count

        return {
            "functions": fn_count,
            "calls_total": call_count,
            "calls_resolved": resolved_count,
            "calls_unresolved": unresolved_count,
        }

    def close(self):
        """Close database connection."""
        self.conn.close()


# ============================================================================
# PythonRepoIndexer — Two-Pass Orchestrator
# ============================================================================


class PythonRepoIndexer:
    """Orchestrates two-pass indexing of a Python repository."""

    SKIP_DIRS = {
        ".venv", "venv", "env", "__pycache__", ".git", ".hg",
        "node_modules", "dist", "build",
    }

    def __init__(self, repo_path: str, db_path: str):
        """
        Initialize indexer with repo and database paths.

        Args:
            repo_path: Path to the Python repository
            db_path: Path to the SQLite database
        """
        self.repo_path = Path(repo_path)
        self.db = IndexerDB(db_path)
        self.parser = TreeSitterParser("python")

    def run(self):
        """Execute full two-pass indexing."""
        print(f"📂 Indexing repository: {self.repo_path}")

        # Pass 1: Parse and insert functions + unresolved calls
        print("📝 Pass 1: Parsing and inserting functions...")
        file_count = self.pass_1_parse_and_insert()
        print(f"✓ Parsed {file_count} files")

        # Pass 2: Resolve cross-file calls
        print("🔗 Pass 2: Resolving cross-file calls...")
        resolved_count = self.pass_2_resolve_calls()
        print(f"✓ Resolved {resolved_count} calls")

        # Print statistics
        stats = self.db.get_stats()
        print()
        print("📊 Indexing complete:")
        print(f"   Functions: {stats['functions']}")
        print(f"   Calls (total): {stats['calls_total']}")
        print(f"   Calls (resolved): {stats['calls_resolved']}")
        print(f"   Calls (unresolved): {stats['calls_unresolved']}")
        print(f"   Database: {self.db.db_path}")

        self.db.close()

    def pass_1_parse_and_insert(self) -> int:
        """
        Walk repo, parse all .py files, insert functions + unresolved calls.

        Returns:
            Number of files successfully parsed
        """
        file_count = 0
        error_count = 0

        for py_file in self.repo_path.rglob("*.py"):
            # Skip excluded directories
            if self._should_skip(py_file):
                continue

            try:
                parsed = self.parser.parse(py_file)
                self.db.insert_functions(parsed.functions, str(self.repo_path))
                self.db.insert_calls_unresolved(parsed.calls)
                file_count += 1
            except Exception as e:
                error_count += 1
                print(f"⚠️  Skipped {py_file}: {e}")

        if error_count > 0:
            print(f"⚠️  {error_count} file(s) skipped due to parsing errors")

        return file_count

    def pass_2_resolve_calls(self) -> int:
        """Resolve callee_name → callee_uid for all unresolved calls.

        Returns:
            Number of calls successfully resolved
        """
        unresolved_calls = self.db.get_unresolved_calls()
        resolutions = []

        for call in unresolved_calls:
            uid = self.db.find_function_uid_by_name(call.callee_name)
            if uid:
                resolutions.append((uid, call.id))

        self.db.resolve_calls_batch(resolutions)
        return len(resolutions)

    def _should_skip(self, file_path: Path) -> bool:
        """Check if a file should be skipped based on its path."""
        for part in file_path.parts:
            if part in self.SKIP_DIRS or part.endswith(".egg-info"):
                return True
        return False
