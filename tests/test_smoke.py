"""Phase 1 smoke tests — verify tree-sitter 0.25.x parses Python correctly.

API note (tree-sitter 0.25+):
  - Use Query(lang, pattern) constructor, not lang.query()
  - Use QueryCursor(query).captures(node) → {capture_name: [nodes]}
  - Use QueryCursor(query).matches(node)  → [(pattern_idx, {capture_name: [nodes]})]
"""
import pytest
from tree_sitter import Query, QueryCursor
from tree_sitter_language_pack import get_language, get_parser


SAMPLE = b"""
def greet(name: str) -> str:
    return f"hello, {name}"

class Greeter:
    def __init__(self, prefix: str):
        self.prefix = prefix

    def greet(self, name: str) -> str:
        return greet(name)

def main():
    g = Greeter("hi")
    result = g.greet("world")
    return result
"""


@pytest.fixture(scope="module")
def lang():
    return get_language("python")


@pytest.fixture(scope="module")
def tree(lang):
    return get_parser("python").parse(SAMPLE)


def captures(lang, pattern, node):
    """Helper: run a query and return {capture_name: [nodes]}."""
    return QueryCursor(Query(lang, pattern)).captures(node)


# --- basic setup ---

def test_language_loads(lang):
    assert lang is not None


def test_parser_creates():
    assert get_parser("python") is not None


def test_parse_returns_tree(tree):
    assert tree is not None
    assert tree.root_node is not None


def test_root_node_has_no_errors(tree):
    assert not tree.root_node.has_error, "tree-sitter reported parse errors"


# --- query API ---

def test_finds_function_definitions(lang, tree):
    result = captures(lang, "(function_definition name: (identifier) @fn_name)", tree.root_node)
    names = [n.text.decode() for n in result.get("fn_name", [])]
    assert "greet" in names
    assert "main" in names
    assert "__init__" in names


def test_finds_function_calls(lang, tree):
    result = captures(lang, "(call function: (identifier) @callee)", tree.root_node)
    callees = [n.text.decode() for n in result.get("callee", [])]
    assert "greet" in callees


def test_line_numbers_present(lang, tree):
    result = captures(lang, "(function_definition name: (identifier) @fn_name)", tree.root_node)
    for node in result.get("fn_name", []):
        assert node.start_point[0] >= 0
        assert node.end_point[0] >= node.start_point[0]


def test_source_slice_from_byte_offsets(lang, tree):
    result = captures(lang, "(function_definition) @fn", tree.root_node)
    for node in result.get("fn", []):
        src = SAMPLE[node.start_byte:node.end_byte].decode()
        assert src.strip().startswith("def")


def test_attribute_calls_detected(lang, tree):
    # g.greet("world") — attribute call
    result = captures(
        lang,
        "(call function: (attribute attribute: (identifier) @method))",
        tree.root_node,
    )
    methods = [n.text.decode() for n in result.get("method", [])]
    assert "greet" in methods


def test_class_definition_detected(lang, tree):
    result = captures(lang, "(class_definition name: (identifier) @cls)", tree.root_node)
    classes = [n.text.decode() for n in result.get("cls", [])]
    assert "Greeter" in classes
