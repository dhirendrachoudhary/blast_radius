"""Python tree-sitter parser — adapted from CGC tools/languages/python.py."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import ast

from tree_sitter import Language, Parser, Query, QueryCursor


# ============================================================================
# Dataclasses — represent parsed file structure
# ============================================================================


@dataclass
class FunctionNode:
    """Represents a function extracted from source code."""

    uid: str  # "{file_path}::{name}::{line_start}"
    name: str
    file_path: str
    line_start: int
    line_end: int
    source: str  # Full function source code
    complexity: int  # Cyclomatic complexity
    decorators: list[str]  # ["@property", "@cache", ...]
    docstring: Optional[str]


@dataclass
class CallEdge:
    """Represents a function call (edge in the call graph)."""

    caller_uid: str  # Which function contains this call
    callee_name: str  # Name of called function (unresolved)
    line_number: int  # Line where call occurs


@dataclass
class ParsedFile:
    """Result of parsing a single Python file."""

    path: str
    functions: list[FunctionNode]
    calls: list[CallEdge]


# ============================================================================
# Tree-sitter queries for Python
# ============================================================================

PY_QUERIES = {
    "imports": """
        (import_statement name: (_) @import)
        (import_from_statement) @from_import_stmt
    """,
    "classes": """
        (class_definition
            name: (identifier) @name
            superclasses: (argument_list)? @superclasses
            body: (block) @body)
    """,
    "functions": """
        (function_definition
            name: (identifier) @name
            parameters: (parameters) @parameters
            body: (block) @body
            return_type: (_)? @return_type)
    """,
    "calls": """
        (call
            function: (identifier) @name)
        (call
            function: (attribute attribute: (identifier) @name) @full_call)
    """,
    "variables": """
        (assignment
            left: (identifier) @name)
    """,
    "lambda_assignments": """
        (assignment
            left: (identifier) @name
            right: (lambda) @lambda_node)
    """,
    "docstrings": """
        (expression_statement (string) @docstring)
    """,
}


# ============================================================================
# PythonTreeSitterParser — extract functions and calls from Python code
# ============================================================================


class PythonTreeSitterParser:
    """A Python-specific parser using tree-sitter."""

    def __init__(self, language_obj: Language, parser_obj: Parser):
        """
        Initialize the parser.

        Args:
            language_obj: Tree-sitter Language object for Python
            parser_obj: Tree-sitter Parser instance
        """
        self.language = language_obj
        self.parser = parser_obj

    def parse(self, file_path: Path | str) -> ParsedFile:
        """
        Parse a single Python file and return structured representation.

        Args:
            file_path: Path to the Python file

        Returns:
            ParsedFile with extracted functions and calls
        """
        file_path = Path(file_path)
        with open(file_path, "r", encoding="utf-8") as f:
            source_code = f.read()

        tree = self.parser.parse(bytes(source_code, "utf8"))
        functions = self._find_functions(tree.root_node, file_path, source_code)
        calls = self._find_calls(tree.root_node, functions, file_path)

        return ParsedFile(path=str(file_path), functions=functions, calls=calls)

    def _get_node_text(self, node) -> str:
        """Extract UTF-8 text from a tree-sitter node."""
        return node.text.decode("utf-8")

    def _get_source_slice(self, source_code: str, start_line: int, end_line: int) -> str:
        """
        Extract source code slice by line range (1-indexed).

        Args:
            source_code: Full source code
            start_line: Starting line (1-indexed)
            end_line: Ending line (1-indexed, inclusive)

        Returns:
            Source code for the line range
        """
        lines = source_code.split("\n")
        # Convert to 0-indexed
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        return "\n".join(lines[start_idx:end_idx])

    def _calculate_complexity(self, node) -> int:
        """
        Calculate cyclomatic complexity by counting control-flow nodes.

        Base complexity is 1 for any function.
        Each if/for/while/try adds 1 to complexity.
        """
        complexity_nodes = {
            "if_statement",
            "for_statement",
            "while_statement",
            "except_clause",
            "with_statement",
            "boolean_operator",
            "list_comprehension",
            "generator_expression",
            "case_clause",
        }
        count = 1

        def traverse(n):
            nonlocal count
            if n.type in complexity_nodes:
                count += 1
            for child in n.children:
                traverse(child)

        traverse(node)
        return count

    def _get_docstring(self, body_node) -> Optional[str]:
        """Extract docstring from a function/class body if present."""
        if body_node and body_node.child_count > 0:
            first_child = body_node.children[0]
            if (
                first_child.type == "expression_statement"
                and first_child.children[0].type == "string"
            ):
                try:
                    return ast.literal_eval(
                        self._get_node_text(first_child.children[0])
                    )
                except (ValueError, SyntaxError):
                    return self._get_node_text(first_child.children[0])
        return None

    def _find_functions(
        self, root_node, file_path: Path, source_code: str
    ) -> list[FunctionNode]:
        """
        Extract all functions from the AST.

        Returns list of FunctionNode objects with:
        - uid: unique identifier
        - name, file_path, line_start, line_end
        - source: full function source code
        - complexity: cyclomatic complexity
        - decorators: list of decorator names
        - docstring: extracted docstring if present
        """
        functions = []
        query_str = PY_QUERIES["functions"]

        # Execute query using tree-sitter 0.25.x API
        query = Query(self.language, query_str)
        cursor = QueryCursor(query)

        for pattern_idx, captures_dict in cursor.matches(root_node):
            # Get the name node
            if "name" not in captures_dict:
                continue

            name_nodes = captures_dict["name"]
            if not name_nodes:
                continue

            name_node = name_nodes[0]
            name = self._get_node_text(name_node)

            # Get the full function_definition node (parent of name)
            func_node = name_node.parent
            while func_node and func_node.type != "function_definition":
                func_node = func_node.parent

            if not func_node:
                continue

            line_start = func_node.start_point[0] + 1  # tree-sitter is 0-indexed
            line_end = func_node.end_point[0] + 1
            source = self._get_source_slice(source_code, line_start, line_end)

            # Extract parameters
            params_node = func_node.child_by_field_name("parameters")
            args = []
            if params_node:
                for p in params_node.children:
                    arg_text = None
                    if p.type == "identifier":
                        # Simple parameter: def foo(x)
                        arg_text = self._get_node_text(p)
                    elif p.type == "default_parameter":
                        # Parameter with default: def foo(x=5)
                        name_node_param = p.child_by_field_name("name")
                        if name_node_param:
                            arg_text = self._get_node_text(name_node_param)
                    elif p.type == "typed_parameter":
                        # Typed parameter: def foo(x: int)
                        name_node_param = p.child_by_field_name("name")
                        if name_node_param:
                            arg_text = self._get_node_text(name_node_param)
                    elif p.type == "typed_default_parameter":
                        # Typed parameter with default: def foo(x: int = 5)
                        name_node_param = p.child_by_field_name("name")
                        if name_node_param:
                            arg_text = self._get_node_text(name_node_param)
                    elif p.type in ("list_splat_pattern", "dictionary_splat_pattern"):
                        # *args or **kwargs
                        arg_text = self._get_node_text(p)

                    if arg_text:
                        args.append(arg_text)

            # Extract decorators
            decorators = []
            for child in func_node.children:
                if child.type == "decorator":
                    decorators.append(self._get_node_text(child))

            # Extract docstring
            body_node = func_node.child_by_field_name("body")
            docstring = self._get_docstring(body_node)

            # Calculate complexity
            complexity = self._calculate_complexity(func_node)

            # Build uid
            uid = f"{file_path}::{name}::{line_start}"

            # Create FunctionNode
            func_data = FunctionNode(
                uid=uid,
                name=name,
                file_path=str(file_path),
                line_start=line_start,
                line_end=line_end,
                source=source,
                complexity=complexity,
                decorators=[d for d in decorators if d],
                docstring=docstring,
            )

            functions.append(func_data)

        return functions

    def _find_calls(
        self, root_node, functions: list[FunctionNode], file_path: Path
    ) -> list[CallEdge]:
        """
        Extract all function calls from the AST.

        For each call, determines:
        - caller_uid: which function (by line lookup) contains this call
        - callee_name: the name of the called function
        - line_number: where the call occurs

        Returns list of CallEdge objects.
        """
        calls = []
        query_str = PY_QUERIES["calls"]

        # Build a line-to-function mapping for fast lookup
        line_to_func = {}
        for func in functions:
            for line in range(func.line_start, func.line_end + 1):
                line_to_func[line] = func

        query = Query(self.language, query_str)
        cursor = QueryCursor(query)

        for pattern_idx, captures_dict in cursor.matches(root_node):
            if "name" not in captures_dict:
                continue

            name_nodes = captures_dict["name"]
            if not name_nodes:
                continue

            name_node = name_nodes[0]
            call_line = name_node.start_point[0] + 1

            # Look up which function contains this call
            caller_func = line_to_func.get(call_line)
            if not caller_func:
                # Fuzzy fallback: widen search to ±3 lines
                for delta in range(1, 4):
                    caller_func = line_to_func.get(call_line - delta)
                    if caller_func:
                        break
                    caller_func = line_to_func.get(call_line + delta)
                    if caller_func:
                        break

            if not caller_func:
                # Skip calls that can't be attributed to a function
                continue

            callee_name = self._get_node_text(name_node)

            call_edge = CallEdge(
                caller_uid=caller_func.uid,
                callee_name=callee_name,
                line_number=call_line,
            )

            calls.append(call_edge)

        return calls


def pre_scan_python(files: list[Path], language_obj: Language, parser_obj: Parser) -> dict:
    """
    Scan Python files to create a map of class/function names to their file paths.

    Used for cross-file call resolution in Phase 3 indexer.

    Args:
        files: List of Python file paths to scan
        language_obj: Tree-sitter Language for Python
        parser_obj: Tree-sitter Parser instance

    Returns:
        Dict mapping {name: [file_paths]} for all functions and classes
    """
    imports_map = {}
    query_str = """
        (class_definition name: (identifier) @name)
        (function_definition name: (identifier) @name)
    """

    query = Query(language_obj, query_str)

    for path in files:
        try:
            with open(path, "r", encoding="utf-8") as f:
                source_code = f.read()

            tree = parser_obj.parse(bytes(source_code, "utf8"))

            cursor = QueryCursor(query)
            for pattern_idx, captures_dict in cursor.matches(tree.root_node):
                if "name" not in captures_dict:
                    continue
                name_nodes = captures_dict["name"]
                for name_node in name_nodes:
                    name = name_node.text.decode("utf-8")
                    if name not in imports_map:
                        imports_map[name] = []
                    imports_map[name].append(str(path.resolve()))

        except Exception as e:
            # Log but continue scanning other files
            pass

    return imports_map
