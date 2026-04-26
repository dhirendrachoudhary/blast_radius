"""Parse git diff output into ChangedRange objects."""

import subprocess
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class ChangedRange:
    """Represents a file with changed line numbers."""

    file_path: str
    lines: list[int] = field(default_factory=list)

    def __post_init__(self):
        """Ensure lines are sorted and deduplicated."""
        object.__setattr__(self, "lines", sorted(set(self.lines)))


class GitDiffParser:
    """Parse git diff output into ChangedRange objects."""

    def __init__(self, repo_path: str):
        """
        Initialize parser with repo path.

        Args:
            repo_path: Path to the git repository
        """
        self.repo_path = Path(repo_path).resolve()

    def get_changed_ranges(self) -> list[ChangedRange]:
        """
        Run 'git diff HEAD --unified=0' and parse output into ChangedRange objects.

        Returns:
            List of ChangedRange objects (one per changed file)
        """
        try:
            # Run git diff with minimal context (--unified=0)
            result = subprocess.run(
                ["git", "diff", "--unified=0", "HEAD"],
                cwd=str(self.repo_path),
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            print(f"⚠️  Git diff failed: {e.stderr}")
            return []
        except FileNotFoundError:
            print("⚠️  Git not found in PATH")
            return []

        return self._parse_unified_diff(result.stdout)

    def _parse_unified_diff(self, diff_output: str) -> list[ChangedRange]:
        """
        Parse unified diff output into ChangedRange objects.

        Args:
            diff_output: Output from 'git diff --unified=0'

        Returns:
            List of ChangedRange objects
        """
        changed_files: dict[str, list[int]] = {}
        current_file: Optional[str] = None

        for line in diff_output.split("\n"):
            # Check for new file marker (e.g., "diff --git a/file.py b/file.py")
            if line.startswith("diff --git"):
                # Extract the target file path (b/...)
                match = re.search(r"b/(.+)$", line)
                if match:
                    file_path = match.group(1)
                    # Convert to absolute path to match database format
                    # (database paths depend on how indexer was invoked)
                    abs_path = str((self.repo_path / file_path).resolve())
                    current_file = abs_path
                    if current_file not in changed_files:
                        changed_files[current_file] = []

            # Check for hunk headers (e.g., "@@ -10,3 +15,4 @@")
            elif line.startswith("@@") and current_file:
                lines = self._parse_hunk_header(line)
                if lines:
                    changed_files[current_file].extend(lines)

        # Convert to ChangedRange objects
        return [
            ChangedRange(file_path, lines)
            for file_path, lines in changed_files.items()
            if lines  # Only include files with actual changes
        ]

    @staticmethod
    def _parse_hunk_header(hunk_line: str) -> list[int]:
        """
        Parse a hunk header and return list of changed line numbers.

        Hunk header format: @@ -a,b +c,d @@
        - a = start line in old file
        - b = number of lines in old file
        - c = start line in new file
        - d = number of lines in new file

        We only care about c and d (new file side).

        Args:
            hunk_line: The hunk header line (e.g., "@@ -10,3 +15,4 @@")

        Returns:
            List of line numbers affected in the new file
        """
        # Extract +c,d or +c from the hunk header
        match = re.search(r"\+(\d+)(?:,(\d+))?", hunk_line)
        if not match:
            return []

        start_line = int(match.group(1))
        num_lines = int(match.group(2)) if match.group(2) else 1

        # Generate list of affected line numbers
        return list(range(start_line, start_line + num_lines))
