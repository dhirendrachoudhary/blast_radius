"""TreeSitterParser dispatcher — adapted from CGC tools/graph_builder.py."""

from pathlib import Path

from .tree_sitter_manager import create_parser, get_language_safe
from .python import PythonTreeSitterParser, ParsedFile


class TreeSitterParser:
    """Dispatcher that routes language-specific parsing."""

    def __init__(self, language: str = "python"):
        """
        Initialize the parser for the specified language.

        Args:
            language: Language name (e.g., "python", "py")

        Raises:
            ValueError: If language is not yet supported
        """
        self.language = language
        self.language_obj = get_language_safe(language)
        self.parser_obj = create_parser(language)

        if language.lower() in ("python", "py"):
            self.extractor = PythonTreeSitterParser(self.language_obj, self.parser_obj)
        else:
            raise ValueError(
                f"Language '{language}' not yet supported. "
                f"Currently supported: python"
            )

    def parse(self, file_path: Path | str) -> ParsedFile:
        """
        Parse a file and return structured representation.

        Args:
            file_path: Path to the source file

        Returns:
            ParsedFile with extracted functions and calls
        """
        return self.extractor.parse(file_path)
