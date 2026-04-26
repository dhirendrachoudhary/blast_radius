"""Resolve changed line ranges to FunctionNode objects via SQLite graph lookup."""

from pathlib import Path
from typing import Optional

from .git_diff import ChangedRange
from .graph import CodeGraph
from .parser.python import FunctionNode


class FunctionResolver:
    """Resolve changed lines to changed functions via CodeGraph."""

    FUZZY_SEARCH_RADIUS = 3

    def __init__(self, graph: CodeGraph):
        self.graph = graph

    def resolve_to_functions(self, changed_ranges: list[ChangedRange]) -> list[FunctionNode]:
        """
        Map changed line ranges to the functions that contain them.

        Tries exact match first; falls back to ±3 line fuzzy search.
        Returns deduplicated functions sorted by uid.
        """
        if not changed_ranges:
            return []

        changed_functions: dict[str, FunctionNode] = {}

        for changed_range in changed_ranges:
            for line_num in changed_range.lines:
                fn = self._find_exact(changed_range.file_path, line_num)
                if fn is None:
                    fn = self._find_fuzzy(changed_range.file_path, line_num)
                if fn:
                    changed_functions[fn.uid] = fn

        return sorted(changed_functions.values(), key=lambda fn: fn.uid)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_path_variants(self, file_path: str) -> list[str]:
        """Return candidate path strings to try when querying the graph.

        Handles macOS symlinks (/var ↔ /private/var) and relative vs absolute
        paths that may differ between index time and diff time.
        """
        variants = [file_path]

        resolved = str(Path(file_path).resolve())
        if resolved != file_path:
            variants.append(resolved)

        if "/private/var" in file_path:
            variants.append(file_path.replace("/private/var", "/var"))
        elif "/var/folders" in file_path:
            variants.append(file_path.replace("/var", "/private/var", 1))

        if Path(file_path).is_absolute():
            parts = Path(file_path).parts
            for i, part in enumerate(parts):
                if part in ("src", "tests", "blast_radius"):
                    rel = "/".join(parts[i:])
                    if rel not in variants:
                        variants.append(rel)

        return variants

    def _find_exact(self, file_path: str, line: int) -> Optional[FunctionNode]:
        for path in self._get_path_variants(file_path):
            fn = self.graph.find_by_line(path, line)
            if fn:
                return fn
        return None

    def _find_fuzzy(self, file_path: str, line: int) -> Optional[FunctionNode]:
        for path in self._get_path_variants(file_path):
            for delta in range(1, self.FUZZY_SEARCH_RADIUS + 1):
                for candidate_line in (line - delta, line + delta):
                    fn = self.graph.find_by_line(path, candidate_line)
                    if fn:
                        print(
                            f"Warning: fuzzy match line {line} → "
                            f"{fn.name}@{fn.file_path}:{fn.line_start} (±{delta}). "
                            "Index may be stale; re-run 'blast-radius index'."
                        )
                        return fn
        return None
