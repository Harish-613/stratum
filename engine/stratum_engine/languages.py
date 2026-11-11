"""Tree-sitter grammars for each supported language, keyed by file extension."""

import tree_sitter_go as tsgo
import tree_sitter_java as tsjava
import tree_sitter_javascript as tsjs
import tree_sitter_python as tspython
import tree_sitter_typescript as tsts
from tree_sitter import Language, Parser

_GRAMMARS = {
    "python": Language(tspython.language()),
    "javascript": Language(tsjs.language()),
    "typescript": Language(tsts.language_typescript()),
    "tsx": Language(tsts.language_tsx()),
    "go": Language(tsgo.language()),
    "java": Language(tsjava.language()),
}

EXTENSIONS = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".tsx": "tsx",
    ".go": "go",
    ".java": "java",
}

_parsers = {name: Parser(lang) for name, lang in _GRAMMARS.items()}


def language_for(path: str) -> str | None:
    """Language name for a file path, or None if Stratum doesn't parse it."""
    dot = path.rfind(".")
    return EXTENSIONS.get(path[dot:].lower()) if dot != -1 else None


def parser_for(language: str) -> Parser:
    return _parsers[language]


def family(language: str) -> str:
    """JavaScript, TypeScript and TSX share import rules, so group them."""
    return "js" if language in ("javascript", "typescript", "tsx") else language
