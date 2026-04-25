"""Unit tests for Phase 2 parser."""

import pytest
from pathlib import Path
import tempfile

from src.blast_radius.parser import TreeSitterParser
from src.blast_radius.parser.python import FunctionNode, CallEdge, ParsedFile


# Sample Python code for testing
SAMPLE_CODE = '''
def simple_function(x):
    """A simple function."""
    return x + 1


def function_with_call(a, b):
    """Function that calls another."""
    result = simple_function(a)
    return result + b


def function_with_complexity(value):
    """Function with control flow."""
    if value > 0:
        for i in range(value):
            if i % 2 == 0:
                print(i)
    return value


class MyClass:
    """A simple class."""

    def method_one(self):
        """First method."""
        return 42

    def method_two(self):
        """Second method that calls another method."""
        return self.method_one() + 1


def calls_class_method():
    """Function that calls a class method."""
    obj = MyClass()
    return obj.method_one()
'''


@pytest.fixture
def parser():
    """Create a TreeSitterParser instance for Python."""
    return TreeSitterParser("python")


@pytest.fixture
def sample_file():
    """Create a temporary Python file with sample code."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(SAMPLE_CODE)
        temp_path = f.name

    yield Path(temp_path)

    # Cleanup
    Path(temp_path).unlink()


class TestTreeSitterParserInit:
    """Test parser initialization."""

    def test_init_python(self, parser):
        """Test initializing parser for Python."""
        assert parser.language == "python"
        assert parser.language_obj is not None
        assert parser.parser_obj is not None

    def test_init_python_alias(self):
        """Test initializing parser with Python alias."""
        parser = TreeSitterParser("py")
        assert parser.language == "py"
        assert parser.language_obj is not None

    def test_init_unsupported_language(self):
        """Test that unsupported languages raise ValueError."""
        with pytest.raises(ValueError, match="not yet supported"):
            TreeSitterParser("javascript")


class TestParseFunctionExtraction:
    """Test function extraction from parsed files."""

    def test_parse_returns_parsed_file(self, parser, sample_file):
        """Test that parse() returns a ParsedFile object."""
        result = parser.parse(sample_file)
        assert isinstance(result, ParsedFile)
        assert result.path == str(sample_file)
        assert isinstance(result.functions, list)
        assert isinstance(result.calls, list)

    def test_function_extraction_count(self, parser, sample_file):
        """Test that all functions are extracted."""
        result = parser.parse(sample_file)
        # Expected: simple_function, function_with_call, function_with_complexity,
        #           MyClass.method_one, MyClass.method_two, calls_class_method
        assert len(result.functions) >= 5, f"Expected at least 5 functions, got {len(result.functions)}"

    def test_function_node_structure(self, parser, sample_file):
        """Test that FunctionNode has all required fields."""
        result = parser.parse(sample_file)
        assert len(result.functions) > 0

        func = result.functions[0]
        assert isinstance(func, FunctionNode)
        assert hasattr(func, "uid")
        assert hasattr(func, "name")
        assert hasattr(func, "file_path")
        assert hasattr(func, "line_start")
        assert hasattr(func, "line_end")
        assert hasattr(func, "source")
        assert hasattr(func, "complexity")
        assert hasattr(func, "decorators")
        assert hasattr(func, "docstring")

    def test_uid_format(self, parser, sample_file):
        """Test that uid follows the expected format."""
        result = parser.parse(sample_file)
        assert len(result.functions) > 0

        func = result.functions[0]
        # uid should be "{file_path}::{name}::{line_start}"
        parts = func.uid.split("::")
        assert len(parts) == 3
        assert parts[0] == str(sample_file)
        assert parts[1] == func.name
        assert parts[2].isdigit()
        assert int(parts[2]) == func.line_start

    def test_function_name_extraction(self, parser, sample_file):
        """Test that function names are extracted correctly."""
        result = parser.parse(sample_file)
        names = {f.name for f in result.functions}
        assert "simple_function" in names
        assert "function_with_call" in names
        assert "function_with_complexity" in names

    def test_function_line_numbers(self, parser, sample_file):
        """Test that line numbers are extracted correctly."""
        result = parser.parse(sample_file)
        simple_func = next((f for f in result.functions if f.name == "simple_function"), None)
        assert simple_func is not None
        assert simple_func.line_start > 0
        assert simple_func.line_end >= simple_func.line_start

    def test_function_source_extraction(self, parser, sample_file):
        """Test that function source code is extracted."""
        result = parser.parse(sample_file)
        simple_func = next((f for f in result.functions if f.name == "simple_function"), None)
        assert simple_func is not None
        assert len(simple_func.source) > 0
        assert "def simple_function" in simple_func.source

    def test_function_docstring_extraction(self, parser, sample_file):
        """Test that docstrings are extracted."""
        result = parser.parse(sample_file)
        simple_func = next((f for f in result.functions if f.name == "simple_function"), None)
        assert simple_func is not None
        assert simple_func.docstring == "A simple function."

    def test_function_complexity_calculation(self, parser, sample_file):
        """Test cyclomatic complexity calculation."""
        result = parser.parse(sample_file)

        simple_func = next((f for f in result.functions if f.name == "simple_function"), None)
        complex_func = next((f for f in result.functions if f.name == "function_with_complexity"), None)

        assert simple_func is not None
        assert complex_func is not None

        # simple_function should have complexity 1 (no control flow)
        assert simple_func.complexity == 1, f"Expected complexity 1, got {simple_func.complexity}"

        # function_with_complexity should have higher complexity (has if, for, if)
        assert complex_func.complexity > 1, f"Expected complexity > 1, got {complex_func.complexity}"


class TestParseCallExtraction:
    """Test call extraction from parsed files."""

    def test_call_extraction_count(self, parser, sample_file):
        """Test that calls are extracted."""
        result = parser.parse(sample_file)
        assert len(result.calls) >= 1, "Expected at least 1 call"

    def test_call_edge_structure(self, parser, sample_file):
        """Test that CallEdge has all required fields."""
        result = parser.parse(sample_file)
        assert len(result.calls) > 0

        call = result.calls[0]
        assert isinstance(call, CallEdge)
        assert hasattr(call, "caller_uid")
        assert hasattr(call, "callee_name")
        assert hasattr(call, "line_number")

    def test_call_to_function(self, parser, sample_file):
        """Test that calls are correctly attributed to their caller."""
        result = parser.parse(sample_file)

        # Find calls to simple_function
        calls_to_simple = [c for c in result.calls if c.callee_name == "simple_function"]
        assert len(calls_to_simple) >= 1

        # Verify the caller is function_with_call
        call = calls_to_simple[0]
        assert "function_with_call" in call.caller_uid

    def test_call_line_numbers(self, parser, sample_file):
        """Test that call line numbers are positive."""
        result = parser.parse(sample_file)
        for call in result.calls:
            assert call.line_number > 0

    def test_caller_uid_format(self, parser, sample_file):
        """Test that caller_uid follows correct format."""
        result = parser.parse(sample_file)
        assert len(result.calls) > 0

        for call in result.calls:
            # caller_uid should be in format "{file_path}::{name}::{line_start}"
            parts = call.caller_uid.split("::")
            assert len(parts) == 3, f"Expected 3 parts in caller_uid, got {len(parts)}"
            assert parts[0] == str(sample_file)


class TestParseIntegration:
    """Integration tests for the full parser."""

    def test_parse_returns_valid_result(self, parser, sample_file):
        """Test end-to-end parsing."""
        result = parser.parse(sample_file)

        # Should have functions
        assert len(result.functions) > 0

        # Each function should have valid uid
        for func in result.functions:
            assert "::" in func.uid
            parts = func.uid.split("::")
            assert len(parts) == 3

        # Each call should reference an existing function
        caller_uids = {f.uid for f in result.functions}
        for call in result.calls:
            assert call.caller_uid in caller_uids, f"Call from non-existent function: {call.caller_uid}"

    def test_multiple_functions_in_file(self, parser, sample_file):
        """Test parsing multiple functions in same file."""
        result = parser.parse(sample_file)

        # All functions should have the same file_path
        file_paths = {f.file_path for f in result.functions}
        assert len(file_paths) == 1
        assert next(iter(file_paths)) == str(sample_file)

    def test_parse_empty_function(self, parser):
        """Test parsing a minimal Python file."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as f:
            f.write("def empty():\n    pass\n")
            temp_path = Path(f.name)

        try:
            result = parser.parse(temp_path)
            assert len(result.functions) == 1
            assert result.functions[0].name == "empty"
            assert result.functions[0].complexity == 1
        finally:
            temp_path.unlink()
