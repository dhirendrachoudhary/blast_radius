"""CLI entrypoint — index and analyze commands."""

import os
import traceback
from pathlib import Path

import typer
from dotenv import load_dotenv

from .indexer import PythonRepoIndexer
from .git_diff import GitDiffParser
from .resolver import FunctionResolver
from .graph import CodeGraph

app = typer.Typer()


@app.command()
def index(repo: str = typer.Argument(..., help="Path to Python repository to index")):
    """Parse repository and build the SQLite call graph."""
    load_dotenv()
    db_path = os.getenv("BLAST_RADIUS_DB", "data/blast_radius.db")

    try:
        PythonRepoIndexer(repo, db_path).run()
    except KeyboardInterrupt:
        print("\nIndexing cancelled.")
        raise typer.Exit(1)
    except Exception as e:
        print(f"Error during indexing: {e}")
        raise typer.Exit(1)


@app.command()
def analyze(
    repo: str = typer.Argument(..., help="Path to Python repository"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print blast radius without generating tests"),
):
    """Run full pipeline: analyze changes → generate tests → run tests."""
    load_dotenv()
    db_path = os.getenv("BLAST_RADIUS_DB", "data/blast_radius.db")

    graph = CodeGraph(db_path)
    try:
        print(f"Analyzing repository: {repo}")

        diff_parser = GitDiffParser(repo)
        changed_ranges = diff_parser.get_changed_ranges()

        if not changed_ranges:
            print("No changes detected.")
            return

        print(f"Found changes in {len(changed_ranges)} file(s).")

        resolver = FunctionResolver(graph)
        ground_zero = resolver.resolve_to_functions(changed_ranges)

        if not ground_zero:
            print("No functions found in changed lines.")
            return

        print(f"Ground zero: {len(ground_zero)} changed function(s)")
        for fn in ground_zero:
            print(f"  - {fn.name} ({fn.file_path}:{fn.line_start})")

        result = graph.get_blast_radius([fn.name for fn in ground_zero])

        print(f"Blast radius : {len(result.affected_functions)} function(s)")
        print(f"Entry points : {len(result.entry_points)} function(s)")
        print(f"Call chains  : {len(result.call_chains)} chain(s)")

        if result.call_chains:
            print("\nCall chains:")
            for chain in result.call_chains[:10]:
                print(f"  {' → '.join(chain)}")
            if len(result.call_chains) > 10:
                print(f"  ... and {len(result.call_chains) - 10} more")

        if dry_run:
            print("\nDry run: stopping here.")
            return

        print("\nTest generation not yet implemented (Phase 6).")
        raise typer.Exit(1)

    except KeyboardInterrupt:
        print("\nAnalysis cancelled.")
        raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:
        print(f"Error during analysis: {e}")
        traceback.print_exc()
        raise typer.Exit(1)
    finally:
        graph.close()


if __name__ == "__main__":
    app()
