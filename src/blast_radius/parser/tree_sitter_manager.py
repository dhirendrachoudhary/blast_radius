"""Thread-safe tree-sitter language/parser loader — adapted from CGC utils/tree_sitter_manager.py."""
# TODO: Phase 2 — adapt from https://github.com/Unix-Dev-Ops/Code-Graph-Context/blob/main/src/codegraphcontext/utils/tree_sitter_manager.py
# Keep: language caching, create_parser(), execute_query() backward-compat shim
# Strip: CGC-specific imports and debug_log calls
