"""SQLite client for call graph queries using recursive CTEs."""

import sqlite3
import json
from typing import Optional
from dataclasses import dataclass

from .parser.python import FunctionNode


# ============================================================================
# Data Models
# ============================================================================


@dataclass
class BlastRadiusResult:
    """Result of a blast-radius query."""

    ground_zero: list[FunctionNode]        # The changed functions (input)
    affected_functions: list[FunctionNode] # All upstream (blast radius set)
    entry_points: list[FunctionNode]       # Functions with no callers above them
    call_chains: list[list[str]]           # Human-readable paths to entry points


# ============================================================================
# CodeGraph — SQLite Client
# ============================================================================


class CodeGraph:
    """SQLite client for call graph queries using recursive CTEs."""

    def __init__(self, db_path: str):
        """
        Initialize connection to SQLite graph database.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row

    def _row_to_function_node(self, row: sqlite3.Row) -> FunctionNode:
        """
        Convert SQL row to FunctionNode object.

        Args:
            row: sqlite3.Row from functions table

        Returns:
            FunctionNode with parsed fields
        """
        decorators = []
        if row["decorators"]:
            try:
                decorators = json.loads(row["decorators"])
            except (json.JSONDecodeError, TypeError):
                decorators = []

        return FunctionNode(
            uid=row["uid"],
            name=row["name"],
            file_path=row["file_path"],
            line_start=row["line_start"],
            line_end=row["line_end"],
            source=row["source"],
            complexity=row["complexity"],
            decorators=decorators,
            docstring=row["docstring"],
        )

    def find_by_name(self, name: str) -> Optional[FunctionNode]:
        """
        Find a function by name (returns first match).

        Args:
            name: Function name to search for

        Returns:
            FunctionNode if found, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT uid, name, file_path, line_start, line_end, source, complexity, decorators, docstring
            FROM functions WHERE name = ? LIMIT 1
            """,
            (name,),
        )
        row = cursor.fetchone()
        return self._row_to_function_node(row) if row else None

    def find_by_line(self, file_path: str, line: int) -> Optional[FunctionNode]:
        """
        Find the function containing a given line.

        Args:
            file_path: Path to the file
            line: Line number

        Returns:
            FunctionNode if found, None otherwise
        """
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT uid, name, file_path, line_start, line_end, source, complexity, decorators, docstring
            FROM functions
            WHERE file_path = ? AND line_start <= ? AND line_end >= ?
            LIMIT 1
            """,
            (file_path, line, line),
        )
        row = cursor.fetchone()
        return self._row_to_function_node(row) if row else None

    def get_blast_radius(self, fn_names: list[str]) -> BlastRadiusResult:
        """
        Find all functions affected by changes to fn_names (main query).

        Args:
            fn_names: List of changed function names

        Returns:
            BlastRadiusResult with ground_zero, affected_functions, entry_points, call_chains
        """
        # Get ground zero functions (the changed functions)
        ground_zero = []
        for name in fn_names:
            fn = self.find_by_name(name)
            if fn:
                ground_zero.append(fn)

        if not ground_zero:
            # No functions found; return empty result
            return BlastRadiusResult(
                ground_zero=[],
                affected_functions=[],
                entry_points=[],
                call_chains=[],
            )

        # Get all affected functions (ground zero + upstream callers)
        affected_uids = self._get_affected_functions(fn_names)
        affected_functions = self._get_functions_by_uids(affected_uids)

        # Get entry points (affected functions with no callers above them)
        entry_points = self._get_entry_points(affected_uids)

        # Get call chains (paths from changed functions to entry points)
        call_chains = self._get_call_chains(fn_names)

        return BlastRadiusResult(
            ground_zero=ground_zero,
            affected_functions=affected_functions,
            entry_points=entry_points,
            call_chains=call_chains,
        )

    def _get_affected_functions(self, fn_names: list[str]) -> set[str]:
        """
        Execute blast radius recursive CTE, return set of UIDs.

        Uses recursive CTE to find all upstream callers of the given functions.

        Args:
            fn_names: List of function names to start from

        Returns:
            Set of UIDs for all affected functions
        """
        cursor = self.conn.cursor()

        # Build placeholders for IN clause
        placeholders = ",".join(["?" for _ in fn_names])

        # Recursive CTE to find all upstream callers
        cursor.execute(
            f"""
            WITH RECURSIVE blast_radius(uid, depth) AS (
                -- Anchor: start with the changed functions
                SELECT uid, 0 FROM functions WHERE name IN ({placeholders})

                UNION ALL

                -- Recursive: find all callers of the functions in blast_radius
                SELECT c.caller_uid, br.depth + 1
                FROM calls c
                JOIN blast_radius br ON br.uid = c.callee_uid
                WHERE br.depth < 10
            )
            SELECT DISTINCT uid FROM blast_radius
            """,
            fn_names,
        )

        return set(row["uid"] for row in cursor.fetchall())

    def _get_functions_by_uids(self, uids: set[str]) -> list[FunctionNode]:
        """
        Get FunctionNode objects for the given UIDs.

        Args:
            uids: Set of function UIDs

        Returns:
            List of FunctionNode objects
        """
        if not uids:
            return []

        cursor = self.conn.cursor()
        placeholders = ",".join(["?" for _ in uids])

        cursor.execute(
            f"""
            SELECT uid, name, file_path, line_start, line_end, source, complexity, decorators, docstring
            FROM functions
            WHERE uid IN ({placeholders})
            """,
            list(uids),
        )

        return [self._row_to_function_node(row) for row in cursor.fetchall()]

    def _get_entry_points(self, affected_uids: set[str]) -> list[FunctionNode]:
        """Blast-radius members that nobody calls — the top of the call chain."""
        if not affected_uids:
            return []

        cursor = self.conn.cursor()
        affected_list = list(affected_uids)
        placeholders = ",".join(["?" for _ in affected_list])

        # Entry points: in blast radius AND never appear as callee_uid anywhere
        cursor.execute(
            f"""
            SELECT f.uid, f.name, f.file_path, f.line_start, f.line_end,
                   f.source, f.complexity, f.decorators, f.docstring
            FROM functions f
            WHERE f.uid IN ({placeholders})
              AND f.uid NOT IN (
                  SELECT DISTINCT callee_uid FROM calls WHERE callee_uid IS NOT NULL
              )
            """,
            affected_list,
        )

        return [self._row_to_function_node(row) for row in cursor.fetchall()]

    def _get_call_chains(self, fn_names: list[str]) -> list[list[str]]:
        """
        Get call paths from changed functions to entry points.

        Uses recursive CTE with path accumulation to build human-readable
        call chains showing how changed functions are called upstream.

        Args:
            fn_names: List of changed function names

        Returns:
            List of call chains, where each chain is a list of function names
        """
        cursor = self.conn.cursor()

        # Build placeholders for IN clause
        placeholders = ",".join(["?" for _ in fn_names])

        # Recursive CTE to build call chains with path accumulation
        cursor.execute(
            f"""
            WITH RECURSIVE chains(uid, name, path_str, depth) AS (
                -- Anchor: start with the changed functions
                SELECT uid, name, name, 0 FROM functions WHERE name IN ({placeholders})

                UNION ALL

                -- Recursive: find callers and append to path
                SELECT f.uid, f.name, f.name || ' → ' || ch.path_str, ch.depth + 1
                FROM functions f
                JOIN calls c2 ON c2.caller_uid = f.uid
                JOIN chains ch ON ch.uid = c2.callee_uid
                WHERE ch.depth < 10
            )
            SELECT DISTINCT path_str FROM chains WHERE depth > 0
            """,
            fn_names,
        )

        # Parse path strings into lists
        chains = []
        for row in cursor.fetchall():
            path_str = row["path_str"]
            if path_str:
                # Split by ' → ' delimiter
                chain = [name.strip() for name in path_str.split(" → ")]
                chains.append(chain)

        return chains

    def close(self):
        """Close database connection."""
        self.conn.close()
