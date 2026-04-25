"""Unit tests for Phase 3 indexer."""

import pytest
import sqlite3
import tempfile
from pathlib import Path

from src.blast_radius.indexer import IndexerDB, PythonRepoIndexer
from src.blast_radius.parser.python import FunctionNode, CallEdge


# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test.db"
        db = IndexerDB(str(db_path))
        yield db
        db.close()


@pytest.fixture
def sample_functions():
    """Create sample FunctionNode objects for testing."""
    return [
        FunctionNode(
            uid="module.py::func_a::1",
            name="func_a",
            file_path="module.py",
            line_start=1,
            line_end=5,
            source="def func_a():\n    return 42",
            complexity=1,
            decorators=[],
            docstring="Function A",
        ),
        FunctionNode(
            uid="module.py::func_b::7",
            name="func_b",
            file_path="module.py",
            line_start=7,
            line_end=12,
            source="def func_b():\n    return func_a() + 1",
            complexity=1,
            decorators=["@cache"],
            docstring=None,
        ),
    ]


@pytest.fixture
def sample_calls():
    """Create sample CallEdge objects for testing."""
    return [
        CallEdge(
            caller_uid="module.py::func_b::7",
            callee_name="func_a",
            line_number=8,
        ),
        CallEdge(
            caller_uid="module.py::func_b::7",
            callee_name="print",  # stdlib, unresolvable
            line_number=9,
        ),
    ]


@pytest.fixture
def test_repo_dir(tmp_path):
    """Create a minimal test repository."""
    # Create src directory
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Create main.py
    (src_dir / "main.py").write_text(
        """
def process_data(x):
    return x * 2

def main():
    result = process_data(5)
    print(result)
    return result
"""
    )

    # Create utils.py
    (src_dir / "utils.py").write_text(
        """
def helper_func():
    return 42

def another_helper():
    return helper_func() + 1
"""
    )

    # Create .venv directory to be skipped
    venv_dir = tmp_path / ".venv" / "lib" / "python3.10"
    venv_dir.mkdir(parents=True)
    (venv_dir / "site.py").write_text("# This should be skipped")

    return tmp_path


# ============================================================================
# IndexerDB Tests
# ============================================================================


class TestIndexerDBSchemaInit:
    """Test database schema initialization."""

    def test_schema_created_on_init(self, temp_db):
        """Test that schema is created when database is initialized."""
        # Query to check if tables exist
        cursor = temp_db.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='functions'"
        )
        assert cursor.fetchone() is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='calls'"
        )
        assert cursor.fetchone() is not None

    def test_indexes_created_on_init(self, temp_db):
        """Test that indexes are created."""
        cursor = temp_db.conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'idx_%'"
        )
        indexes = cursor.fetchall()
        assert len(indexes) >= 4  # 4 indexes expected

    def test_schema_idempotent(self, temp_db):
        """Test that calling _init_schema twice doesn't fail."""
        # This shouldn't raise an error
        temp_db._init_schema()
        temp_db._init_schema()

        # Schema should still be valid
        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM functions")
        assert cursor.fetchone() is not None


class TestIndexerDBInsertFunctions:
    """Test function insertion."""

    def test_insert_functions(self, temp_db, sample_functions):
        """Test inserting functions."""
        temp_db.insert_functions(sample_functions, "/repo")

        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM functions")
        count = cursor.fetchone()["count"]
        assert count == 2

    def test_insert_functions_preserves_fields(self, temp_db, sample_functions):
        """Test that function fields are preserved."""
        temp_db.insert_functions(sample_functions, "/repo")

        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT * FROM functions WHERE name='func_a'")
        row = cursor.fetchone()

        assert row["uid"] == "module.py::func_a::1"
        assert row["name"] == "func_a"
        assert row["file_path"] == "module.py"
        assert row["line_start"] == 1
        assert row["line_end"] == 5
        assert "def func_a()" in row["source"]
        assert row["complexity"] == 1
        assert row["docstring"] == "Function A"

    def test_insert_functions_upsert(self, temp_db, sample_functions):
        """Test that functions are upserted on re-insert."""
        temp_db.insert_functions(sample_functions, "/repo")

        # Modify and re-insert
        sample_functions[0].complexity = 5
        temp_db.insert_functions([sample_functions[0]], "/repo")

        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT complexity FROM functions WHERE uid=?", (sample_functions[0].uid,))
        row = cursor.fetchone()
        assert row["complexity"] == 5

        # Should still have only 2 functions
        cursor.execute("SELECT COUNT(*) as count FROM functions")
        count = cursor.fetchone()["count"]
        assert count == 2


class TestIndexerDBInsertCalls:
    """Test call insertion."""

    def test_insert_calls_unresolved(self, temp_db, sample_functions, sample_calls):
        """Test inserting unresolved calls."""
        temp_db.insert_functions(sample_functions, "/repo")
        temp_db.insert_calls_unresolved(sample_calls)

        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT COUNT(*) as count FROM calls")
        count = cursor.fetchone()["count"]
        assert count == 2

    def test_calls_have_null_callee_uid(self, temp_db, sample_functions, sample_calls):
        """Test that inserted calls have NULL callee_uid."""
        temp_db.insert_functions(sample_functions, "/repo")
        temp_db.insert_calls_unresolved(sample_calls)

        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT callee_uid FROM calls")
        rows = cursor.fetchall()

        for row in rows:
            assert row["callee_uid"] is None


class TestIndexerDBResolveCall:
    """Test call resolution (Pass 2)."""

    def test_resolve_call(self, temp_db, sample_functions, sample_calls):
        """Test resolving a call."""
        temp_db.insert_functions(sample_functions, "/repo")
        temp_db.insert_calls_unresolved(sample_calls)

        # Get the first call
        unresolved = temp_db.get_unresolved_calls()
        assert len(unresolved) > 0

        # Resolve it
        temp_db.resolve_call(unresolved[0].id, sample_functions[0].uid)

        # Check it's resolved
        cursor = temp_db.conn.cursor()
        cursor.execute("SELECT callee_uid FROM calls WHERE id=?", (unresolved[0].id,))
        row = cursor.fetchone()
        assert row["callee_uid"] == sample_functions[0].uid

    def test_get_unresolved_calls(self, temp_db, sample_functions, sample_calls):
        """Test fetching unresolved calls."""
        temp_db.insert_functions(sample_functions, "/repo")
        temp_db.insert_calls_unresolved(sample_calls)

        unresolved = temp_db.get_unresolved_calls()
        assert len(unresolved) == 2
        assert unresolved[0].callee_name == "func_a"


class TestIndexerDBFindFunction:
    """Test function lookup."""

    def test_find_function_by_name(self, temp_db, sample_functions):
        """Test finding a function by name."""
        temp_db.insert_functions(sample_functions, "/repo")

        uid = temp_db.find_function_uid_by_name("func_a")
        assert uid == "module.py::func_a::1"

    def test_find_function_not_found(self, temp_db):
        """Test finding a non-existent function."""
        uid = temp_db.find_function_uid_by_name("nonexistent")
        assert uid is None


class TestIndexerDBStats:
    """Test statistics retrieval."""

    def test_get_stats(self, temp_db, sample_functions, sample_calls):
        """Test getting indexing statistics."""
        temp_db.insert_functions(sample_functions, "/repo")
        temp_db.insert_calls_unresolved(sample_calls)

        stats = temp_db.get_stats()
        assert stats["functions"] == 2
        assert stats["calls_total"] == 2
        assert stats["calls_resolved"] == 0
        assert stats["calls_unresolved"] == 2

        # Resolve one call
        unresolved = temp_db.get_unresolved_calls()
        temp_db.resolve_call(unresolved[0].id, sample_functions[0].uid)

        stats = temp_db.get_stats()
        assert stats["calls_resolved"] == 1
        assert stats["calls_unresolved"] == 1


# ============================================================================
# PythonRepoIndexer Tests
# ============================================================================


class TestPythonRepoIndexerSkipDirs:
    """Test directory skipping logic."""

    def test_should_skip_venv(self, test_repo_dir):
        """Test that .venv directory is skipped."""
        db_path = test_repo_dir / "test.db"
        indexer = PythonRepoIndexer(str(test_repo_dir), str(db_path))

        venv_file = test_repo_dir / ".venv" / "lib" / "python3.10" / "site.py"
        assert indexer._should_skip(venv_file)

    def test_should_not_skip_src(self, test_repo_dir):
        """Test that src directory is not skipped."""
        db_path = test_repo_dir / "test.db"
        indexer = PythonRepoIndexer(str(test_repo_dir), str(db_path))

        src_file = test_repo_dir / "src" / "main.py"
        assert not indexer._should_skip(src_file)


class TestPythonRepoIndexerPass1:
    """Test Pass 1 (parse and insert)."""

    def test_pass_1_parses_all_files(self, test_repo_dir):
        """Test that Pass 1 parses all Python files (excluding skipped dirs)."""
        db_path = test_repo_dir / "test.db"
        indexer = PythonRepoIndexer(str(test_repo_dir), str(db_path))

        file_count = indexer.pass_1_parse_and_insert()

        # Should parse 2 files: src/main.py and src/utils.py
        # Should skip .venv/lib/python3.10/site.py
        assert file_count == 2

        stats = indexer.db.get_stats()
        assert stats["functions"] > 0


class TestPythonRepoIndexerPass2:
    """Test Pass 2 (resolve calls)."""

    def test_pass_2_resolves_calls(self, test_repo_dir):
        """Test that Pass 2 resolves user-defined calls."""
        db_path = test_repo_dir / "test.db"
        indexer = PythonRepoIndexer(str(test_repo_dir), str(db_path))

        # Run Pass 1
        indexer.pass_1_parse_and_insert()
        stats_before = indexer.db.get_stats()

        # Run Pass 2
        resolved_count = indexer.pass_2_resolve_calls()

        # Some calls should be resolved (user-defined)
        assert resolved_count > 0

        stats_after = indexer.db.get_stats()
        assert stats_after["calls_resolved"] > stats_before["calls_resolved"]


class TestPythonRepoIndexerEndToEnd:
    """End-to-end tests."""

    def test_full_indexing_pipeline(self, test_repo_dir):
        """Test full indexing pipeline from repo to database."""
        db_path = test_repo_dir / "test.db"
        indexer = PythonRepoIndexer(str(test_repo_dir), str(db_path))

        indexer.run()

        # Verify database was created
        assert db_path.exists()

        # Verify schema and data
        conn = sqlite3.connect(str(db_path))
        cursor = conn.cursor()

        cursor.execute("SELECT COUNT(*) as count FROM functions")
        fn_count = cursor.fetchone()[0]
        assert fn_count > 0

        cursor.execute("SELECT COUNT(*) as count FROM calls")
        call_count = cursor.fetchone()[0]

        # There should be some calls
        assert call_count > 0

        conn.close()
