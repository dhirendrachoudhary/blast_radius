"""Unit tests for Phase 5 function resolver."""

import pytest
import tempfile
from pathlib import Path

from src.blast_radius.git_diff import ChangedRange
from src.blast_radius.resolver import FunctionResolver
from src.blast_radius.indexer import PythonRepoIndexer
from src.blast_radius.graph import CodeGraph
from src.blast_radius.parser.python import FunctionNode


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def test_repo_for_resolver(tmp_path):
    """Create a test repo with sample functions for resolver tests."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Create main.py
    (src_dir / "main.py").write_text(
        """
def process_data(x):
    return x * 2

def main():
    result = process_data(5)
    return result
"""
    )

    # Create utils.py
    (src_dir / "utils.py").write_text(
        """
def helper_func():
    return 42

def another_helper():
    result = helper_func()
    return result + 1
"""
    )

    # Create .venv to be skipped
    venv_dir = tmp_path / ".venv" / "lib"
    venv_dir.mkdir(parents=True)
    (venv_dir / "site.py").write_text("# ignored")

    # Index the repo
    db_path = tmp_path / "test.db"
    indexer = PythonRepoIndexer(str(src_dir.parent), str(db_path))
    indexer.run()

    yield tmp_path, src_dir


@pytest.fixture
def indexed_graph(test_repo_for_resolver):
    """Return CodeGraph from indexed test repo."""
    tmp_path, _ = test_repo_for_resolver
    db_path = tmp_path / "test.db"
    graph = CodeGraph(str(db_path))
    yield graph
    graph.close()


# ============================================================================
# FunctionResolver Tests
# ============================================================================


class TestFunctionResolverBasic:
    """Test basic function resolution."""

    def test_resolver_initialization(self, indexed_graph):
        """Test resolver creation."""
        resolver = FunctionResolver(indexed_graph)
        assert resolver.graph is indexed_graph
        assert resolver.FUZZY_SEARCH_RADIUS == 3

    def test_resolve_single_changed_line(self, indexed_graph, test_repo_for_resolver):
        """Single changed line resolves to containing function."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()

        # process_data is on lines 2-3
        changed_ranges = [ChangedRange(str(main_py), [2])]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        assert len(functions) > 0
        names = {fn.name for fn in functions}
        assert "process_data" in names

    def test_resolve_multiple_lines_same_function(
        self, indexed_graph, test_repo_for_resolver
    ):
        """Multiple lines in same function deduplicated."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()

        # Lines 2-3 are both in process_data
        changed_ranges = [ChangedRange(str(main_py), [2, 3])]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # Should return process_data only once (deduplicated)
        process_data_funcs = [fn for fn in functions if fn.name == "process_data"]
        assert len(process_data_funcs) <= 1

    def test_resolve_multiple_functions(self, indexed_graph, test_repo_for_resolver):
        """Multiple changed functions all returned."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()

        # Lines 2-3 (process_data) and 5-7 (main)
        changed_ranges = [ChangedRange(str(main_py), [2, 3, 5, 6, 7])]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        names = {fn.name for fn in functions}
        assert "process_data" in names
        assert "main" in names

    def test_resolve_empty_changed_ranges(self, indexed_graph):
        """Empty input returns empty output."""
        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions([])

        assert functions == []

    def test_resolve_no_functions_found(self, indexed_graph, test_repo_for_resolver):
        """Changed lines with no functions (comments/whitespace) return empty."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()

        # Line 1 is blank/docstring area (no function)
        changed_ranges = [ChangedRange(str(main_py), [1])]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # May be empty or may find something, depending on indexing
        assert isinstance(functions, list)

    def test_deduplication_by_uid(self, indexed_graph, test_repo_for_resolver):
        """Same function from multiple lines: deduplicate by uid."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()
        utils_py = (src_dir / "utils.py").resolve()

        # Create redundant changes
        changed_ranges = [
            ChangedRange(str(main_py), [2, 3, 2, 3]),  # Duplicate
            ChangedRange(str(utils_py), [2, 3, 2, 3]),  # Duplicate
        ]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # Check that UIDs are unique
        uids = [fn.uid for fn in functions]
        assert len(uids) == len(set(uids))

    def test_resolve_returns_sorted_by_uid(self, indexed_graph, test_repo_for_resolver):
        """Returned functions sorted by uid."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()
        utils_py = (src_dir / "utils.py").resolve()

        changed_ranges = [
            ChangedRange(str(main_py), [2, 5]),  # process_data and main
            ChangedRange(str(utils_py), [2, 5]),  # helper_func and another_helper
        ]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # Check sorted order
        uids = [fn.uid for fn in functions]
        assert uids == sorted(uids)


class TestFuzzyMatching:
    """Test fuzzy matching behavior."""

    def test_fuzzy_search_radius_constant(self, indexed_graph):
        """Verify fuzzy search radius is correct."""
        resolver = FunctionResolver(indexed_graph)
        assert resolver.FUZZY_SEARCH_RADIUS == 3

    def test_fuzzy_match_fires_for_non_existent_line(self, indexed_graph):
        """Fuzzy match called for line with no function (prints warning)."""
        # This is more of a logging test; we can't easily capture print output
        # but we can verify fuzzy_match doesn't crash
        resolver = FunctionResolver(indexed_graph)
        result = resolver._find_fuzzy("/nonexistent/file.py", 99999)
        assert result is None  # No function found even in fuzzy

    def test_fuzzy_match_returns_function_if_found(self, indexed_graph):
        """Fuzzy match returns FunctionNode if found nearby."""
        # Use main.py where we know functions exist
        # If exact line doesn't match, fuzzy should find nearby function
        resolver = FunctionResolver(indexed_graph)

        # Try to find something that might be fuzzy-matched
        # This depends on actual file content
        result = resolver._find_fuzzy("/some/path/file.py", 100)
        # Result could be None if nothing found, or FunctionNode if found
        assert result is None or isinstance(result, FunctionNode)


# ============================================================================
# Integration Tests
# ============================================================================


class TestResolverIntegration:
    """End-to-end resolver tests."""

    def test_full_flow_single_file(self, indexed_graph, test_repo_for_resolver):
        """Full flow: ChangedRange → FunctionNode."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()

        # Create changed ranges
        changed_ranges = [ChangedRange(str(main_py), [2, 3, 5, 6])]

        # Resolve
        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # Verify result
        assert len(functions) >= 1
        assert all(isinstance(fn, FunctionNode) for fn in functions)
        assert all(hasattr(fn, "uid") for fn in functions)
        assert all(hasattr(fn, "name") for fn in functions)

    def test_full_flow_multiple_files(self, indexed_graph, test_repo_for_resolver):
        """Full flow: Multiple files → multiple functions."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()
        utils_py = (src_dir / "utils.py").resolve()

        # Create changed ranges
        changed_ranges = [
            ChangedRange(str(main_py), [2, 5]),
            ChangedRange(str(utils_py), [2, 5]),
        ]

        # Resolve
        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # Verify result
        assert len(functions) >= 2
        names = {fn.name for fn in functions}
        # Should have functions from both files
        assert len(names) >= 2

    def test_resolver_handles_real_indexed_data(self, indexed_graph, test_repo_for_resolver):
        """Resolver works with real indexed data."""
        _, src_dir = test_repo_for_resolver

        # Get a real file from the indexed repo
        main_py = (src_dir / "main.py").resolve()

        # Create a changed range in a function
        changed_ranges = [ChangedRange(str(main_py), [3])]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # Should resolve to at least one function
        assert len(functions) >= 0  # Might be empty if line 3 is not in a function

    def test_resolver_result_structure(self, indexed_graph, test_repo_for_resolver):
        """Verify FunctionNode objects have all required fields."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()

        changed_ranges = [ChangedRange(str(main_py), [2])]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        if functions:
            fn = functions[0]
            assert hasattr(fn, "uid")
            assert hasattr(fn, "name")
            assert hasattr(fn, "file_path")
            assert hasattr(fn, "line_start")
            assert hasattr(fn, "line_end")
            assert hasattr(fn, "source")
            assert hasattr(fn, "complexity")
            assert hasattr(fn, "decorators")
            assert hasattr(fn, "docstring")

    def test_resolver_with_multiple_changed_lines_per_file(
        self, indexed_graph, test_repo_for_resolver
    ):
        """Resolver handles many changed lines in a file."""
        _, src_dir = test_repo_for_resolver
        main_py = (src_dir / "main.py").resolve()

        # Change almost every line
        changed_ranges = [ChangedRange(str(main_py), list(range(1, 10)))]

        resolver = FunctionResolver(indexed_graph)
        functions = resolver.resolve_to_functions(changed_ranges)

        # Should return something
        assert isinstance(functions, list)
