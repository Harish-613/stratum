"""Incremental analysis.

A Session keeps every file's parse result in memory. When files change it
re-parses only those files (skipping any whose content hash is unchanged) and
re-resolves only their imports. The import resolver's indexes are rebuilt
only when the set of files changes or a file that defines module identity
changes (a Java/Go package line, an __init__.py, a go.mod).

The graph-wide passes (Tarjan, reachability, duplicate grouping) still run
over the whole graph on every update, but they are linear-time and cheap next
to parsing, which dominates a full scan.
"""

import hashlib
import time
from pathlib import Path

from stratum_engine.analyze import analyze_from_graph
from stratum_engine.languages import language_for
from stratum_engine.parse import parse_file
from stratum_engine.resolve import Resolver
from stratum_engine.scan import SKIP_DIRS, iter_source_files


def _digest(data: bytes) -> str:
    return hashlib.blake2b(data, digest_size=16).hexdigest()


class Session:
    def __init__(self, root: str):
        self.root = str(Path(root).resolve())
        self.files: dict[str, dict] = {}
        self.hashes: dict[str, str] = {}
        self.edges: dict[str, set] = {}
        self.external: dict[str, list] = {}
        self.resolver: Resolver | None = None
        self.last_timings: dict[str, float] = {}

    # ---------- full scan ----------
    def scan(self) -> dict:
        t0 = time.perf_counter()
        self.files.clear()
        self.hashes.clear()
        for rel, path, language in iter_source_files(self.root):
            data = path.read_bytes()
            self.files[rel] = parse_file(data, language)
            self.hashes[rel] = _digest(data)
        t1 = time.perf_counter()
        self._rebuild_resolver()
        self.edges, self.external = {}, {}
        for rel in self.files:
            self._resolve_one(rel)
        t2 = time.perf_counter()
        report = self.report()
        t3 = time.perf_counter()
        self.last_timings = {"parse_s": t1 - t0, "resolve_s": t2 - t1, "graph_s": t3 - t2, "total_s": t3 - t0}
        return report

    # ---------- incremental update ----------
    def update(self, changed_paths) -> dict:
        """Re-analyze after the given files were created, edited or deleted.

        Paths may be absolute or relative to the root. Returns the new report
        plus {"changed": [...]} listing files whose content really changed.
        """
        t0 = time.perf_counter()
        changed, membership_changed = [], False
        for raw in changed_paths:
            rel = self._relative(raw)
            if rel is None:
                continue
            abs_path = Path(self.root) / rel
            language = language_for(rel)
            if rel.endswith("go.mod"):
                membership_changed = True
                continue
            if language is None:
                continue
            if not abs_path.is_file():
                if rel in self.files:  # deleted
                    del self.files[rel]
                    self.hashes.pop(rel, None)
                    self.edges.pop(rel, None)
                    self.external.pop(rel, None)
                    changed.append(rel)
                    membership_changed = True
                continue
            data = abs_path.read_bytes()
            digest = _digest(data)
            if self.hashes.get(rel) == digest:
                continue  # saved without changes
            old = self.files.get(rel)
            new = parse_file(data, language)
            if old is None or old.get("package") != new.get("package") or rel.endswith("__init__.py"):
                membership_changed = True
            self.files[rel] = new
            self.hashes[rel] = digest
            changed.append(rel)
        t1 = time.perf_counter()

        if membership_changed:
            # Module identity changed: every file's imports may now resolve differently.
            self._rebuild_resolver()
            self.edges, self.external = {}, {}
            for rel in self.files:
                self._resolve_one(rel)
        else:
            for rel in changed:
                self._resolve_one(rel)
        t2 = time.perf_counter()
        report = self.report()
        t3 = time.perf_counter()
        self.last_timings = {"parse_s": t1 - t0, "resolve_s": t2 - t1, "graph_s": t3 - t2, "total_s": t3 - t0,
                             "full_resolve": membership_changed}
        report["changed"] = changed
        return report

    def report(self) -> dict:
        return analyze_from_graph(self.root, self.files, self.edges, self.external)

    # ---------- helpers ----------
    def _rebuild_resolver(self):
        self.resolver = Resolver(self.root, self.files)

    def _resolve_one(self, rel: str):
        targets, ext = set(), set()
        for imp in self.files[rel]["imports"]:
            if imp.get("type_only"):
                continue
            hit = self.resolver.resolve(rel, imp)
            if hit:
                targets.update(hit)
            else:
                ext.add(imp["module"])
        self.edges[rel] = targets
        self.external[rel] = sorted(ext)

    def _relative(self, raw: str) -> str | None:
        p = Path(raw)
        if p.is_absolute():
            try:
                p = p.resolve().relative_to(self.root)
            except ValueError:
                return None  # outside the workspace
        rel = p.as_posix()
        if any(part in SKIP_DIRS for part in Path(rel).parts[:-1]):
            return None
        return rel
