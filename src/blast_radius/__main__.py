"""CLI entrypoint — index and analyze commands (typer)."""

import os
import typer
from pathlib import Path
from dotenv import load_dotenv

from blast_radius.indexer import PythonRepoIndexer

app = typer.Typer()


@app.command()
def index(repo: str = typer.Argument(..., help="Path to Python repository to index")):
    """Parse repository and build the SQLite call graph.

    Example:
        blast-radius index /path/to/your/repo
    """
    load_dotenv()
    db_path = os.getenv("BLAST_RADIUS_DB", "data/blast_radius.db")

    try:
        indexer = PythonRepoIndexer(repo, db_path)
        indexer.run()
    except KeyboardInterrupt:
        print("\n⚠️  Indexing cancelled by user")
        raise typer.Exit(1)
    except Exception as e:
        print(f"❌ Error during indexing: {e}")
        raise typer.Exit(1)


@app.command()
def analyze(
    repo: str = typer.Argument(..., help="Path to Python repository"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print blast radius without generating tests"),
):
    """Run full pipeline: analyze changes → generate tests → run tests.

    Example:
        blast-radius analyze /path/to/your/repo
        blast-radius analyze /path/to/your/repo --dry-run
    """
    # TODO: Phase 5-7 — implement analyze pipeline
    print("❌ analyze command not yet implemented (Phase 5-7)")
    raise typer.Exit(1)


if __name__ == "__main__":
    app()
