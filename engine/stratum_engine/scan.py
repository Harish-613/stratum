from pathlib import Path

from stratum_engine.languages import language_for
from stratum_engine.parse import parse_file, parse_python

SKIP_DIRS = {
    ".venv", "venv", "node_modules", ".git", "__pycache__", "dist", "build",
    "out", "target", "vendor", ".next", ".tox", ".mypy_cache", "coverage",
}


def iter_source_files(root: str):
    """Yield (relative_posix_path, absolute_path, language) for every parseable file."""
    root_path = Path(root)
    for file in sorted(root_path.rglob("*")):
        if not file.is_file():
            continue
        rel = file.relative_to(root_path)
        if any(part in SKIP_DIRS for part in rel.parts[:-1]):
            continue
        language = language_for(file.name)
        if language is None:
            continue
        yield rel.as_posix(), file, language


def scan_folder(root: str) -> dict:
    """Python-only summary per file (the original `stratum scan` output)."""
    return {
        rel: parse_python(path.read_bytes())
        for rel, path, language in iter_source_files(root)
        if language == "python"
    }


def scan_all(root: str) -> dict:
    """Full parse of every supported file, keyed by relative path."""
    return {rel: parse_file(path.read_bytes(), language) for rel, path, language in iter_source_files(root)}
