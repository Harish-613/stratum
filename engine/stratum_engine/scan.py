from pathlib import Path

from stratum_engine.parse import parse_python

SKIP_DIRS = {".venv", "node_modules", ".git", "__pycache__"}


def scan_folder(root: str) -> dict:
    root_path = Path(root)
    results = {}

    for file in sorted(root_path.rglob("*.py")):
        if any(part in SKIP_DIRS for part in file.parts):
            continue
        relative = file.relative_to(root_path).as_posix()
        results[relative] = parse_python(file.read_bytes())

    return results