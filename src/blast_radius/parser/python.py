"""Python tree-sitter parser — adapted from CGC tools/languages/python.py."""
# TODO: Phase 2 — adapt from https://github.com/Unix-Dev-Ops/Code-Graph-Context/blob/main/src/codegraphcontext/tools/languages/python.py
# Keep: PY_QUERIES dict, _find_functions(), _find_calls(), pre_scan_python()
# Strip: all Neo4j / FalkorDB / database writes, JobManager references, notebook nbconvert
