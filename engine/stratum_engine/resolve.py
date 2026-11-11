"""Turn raw import strings into files inside the repo.

Imports of third-party or standard-library code (react, os, fmt,
java.util.List) don't resolve to a repo file and are reported as external.

Rules per language:
  Python      absolute "a.b.c" matches a file whose module path ends in a.b.c
              (so src/ layouts work); relative ".x" / "..x" walk up from the
              importing file's package. "from pkg import mod" tries pkg/mod first.
  JS/TS       only relative specifiers ("./x", "../x"); tries the extensions
              and index files Node and TypeScript would, including the ESM
              convention of writing "./x.js" for a file that is x.ts.
  Go          "module/path/pkg" maps to that directory via go.mod; an import
              depends on every non-test file in the package directory.
  Java        "a.b.C" maps to the file declaring class C in package a.b;
              "a.b.*" maps to every file in package a.b.
"""

from collections import defaultdict
from pathlib import Path, PurePosixPath

from stratum_engine.languages import family
from stratum_engine.scan import SKIP_DIRS

JS_EXTS = [".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts"]


class Resolver:
    def __init__(self, root: str, files: dict):
        """files: {relative_path: parse_file() result}"""
        self.files = files
        self.paths = set(files)
        self._index_python()
        self._index_java()
        self._index_go(root)

    # ---------- indexes ----------
    def _index_python(self):
        # Every dotted suffix of every module path, e.g. src/pkg/mod.py ->
        # "src.pkg.mod", "pkg.mod", "mod". Lookups prefer the shortest path.
        #
        # A suffix only counts if the folder in front of it is NOT a package
        # (has no __init__.py): tests/pkg/flask.py inside package tests/pkg is
        # importable as "pkg.flask" or "tests.pkg.flask", never as plain "flask".
        package_dirs = {
            PurePosixPath(p).parent.as_posix()
            for p, info in self.files.items()
            if info["language"] == "python" and p.endswith("__init__.py")
        }
        self.py_modules = defaultdict(list)
        for path, info in self.files.items():
            if info["language"] != "python":
                continue
            parts = list(PurePosixPath(path).with_suffix("").parts)
            if parts[-1] == "__init__":
                parts = parts[:-1]
            for i in range(len(parts)):
                prefix = "/".join(parts[:i])
                if prefix and prefix in package_dirs:
                    continue
                self.py_modules[".".join(parts[i:])].append(path)
        for paths in self.py_modules.values():
            paths.sort(key=lambda p: (p.count("/"), p))

    def _index_java(self):
        self.java_classes = {}
        self.java_packages = defaultdict(list)
        for path, info in self.files.items():
            if info["language"] != "java":
                continue
            pkg = info.get("package") or ""
            stem = PurePosixPath(path).stem
            fqn = f"{pkg}.{stem}" if pkg else stem
            self.java_classes[fqn] = path
            self.java_packages[pkg].append(path)

    def _index_go(self, root: str):
        # go.mod "module example.com/app" at dir D => import "example.com/app/x" is D/x
        self.go_modules = []
        root_path = Path(root)
        for gomod in root_path.rglob("go.mod"):
            rel_dir = gomod.parent.relative_to(root_path)
            if any(part in SKIP_DIRS for part in rel_dir.parts):
                continue
            for line in gomod.read_text(errors="replace").splitlines():
                line = line.strip()
                if line.startswith("module "):
                    module = line.split()[1].strip('"')
                    self.go_modules.append((module, rel_dir.as_posix()))
                    break
        self.go_modules.sort(key=lambda m: -len(m[0]))  # longest module path wins
        self.go_dirs = defaultdict(list)
        for path, info in self.files.items():
            if info["language"] == "go" and not path.endswith("_test.go"):
                self.go_dirs[PurePosixPath(path).parent.as_posix()].append(path)

    # ---------- resolution ----------
    def resolve(self, importer: str, imp: dict) -> list[str]:
        """Repo files that one import of `importer` refers to ([] if external)."""
        fam = family(self.files[importer]["language"])
        if fam == "python":
            found = self._python(importer, imp)
        elif fam == "js":
            found = self._js(importer, imp["module"])
        elif fam == "go":
            found = self._go(imp["module"])
        else:
            found = self._java(imp)
        return [f for f in found if f != importer]

    def _python(self, importer: str, imp: dict) -> list[str]:
        module, names = imp["module"], imp["names"]
        if module.startswith("."):
            level = len(module) - len(module.lstrip("."))
            rest = module[level:]
            base = PurePosixPath(importer).parent
            for _ in range(level - 1):
                base = base.parent
            base_str = "" if str(base) == "." else str(base)
            target = "/".join(p for p in [base_str, rest.replace(".", "/")] if p)
            if not rest:
                # from . import a, b  -> sibling modules a.py, b.py (or the package itself)
                hits = [h for n in names for h in self._py_file(f"{target}/{n}" if target else n)]
                return hits or self._py_file(target)
            for n in names:
                hit = self._py_file(f"{target}/{n}")
                if hit:
                    return hit
            return self._py_file(target)

        for n in names:  # from pkg import submodule
            hits = self.py_modules.get(f"{module}.{n}")
            if hits:
                return [self._closest(importer, hits)]
        hits = self.py_modules.get(module)
        return [self._closest(importer, hits)] if hits else []

    def _py_file(self, base: str) -> list[str]:
        for candidate in (f"{base}.py", f"{base}/__init__.py"):
            if candidate in self.paths:
                return [candidate]
        return []

    @staticmethod
    def _closest(importer: str, hits: list[str]) -> str:
        # Prefer a module in the importer's own top-level folder.
        top = importer.split("/", 1)[0]
        same = [h for h in hits if h.split("/", 1)[0] == top]
        return (same or hits)[0]

    def _js(self, importer: str, spec: str) -> list[str]:
        if not spec.startswith("."):
            return []  # package import (react, lodash, @scope/pkg)
        joined = PurePosixPath(importer).parent / spec
        parts = []
        for part in joined.parts:
            if part == "..":
                if parts:
                    parts.pop()
            elif part != ".":
                parts.append(part)
        base = "/".join(parts)
        candidates = [base] + [base + e for e in JS_EXTS] + [f"{base}/index{e}" for e in JS_EXTS]
        stem, dot, ext = base.rpartition(".")
        if dot and ext in ("js", "jsx", "mjs", "cjs"):
            candidates += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
        for c in candidates:
            if c in self.paths:
                return [c]
        return []

    def _go(self, path: str) -> list[str]:
        for module, mod_dir in self.go_modules:
            if path == module or path.startswith(module + "/"):
                sub = path[len(module):].lstrip("/")
                directory = "/".join(p for p in [mod_dir if mod_dir != "." else "", sub] if p)
                return list(self.go_dirs.get(directory, []))
        return []

    def _java(self, imp: dict) -> list[str]:
        if imp["names"] == ["*"]:
            return list(self.java_packages.get(imp["module"], []))
        module = imp["module"]
        while module:  # a.b.Outer.Inner -> try a.b.Outer
            if module in self.java_classes:
                return [self.java_classes[module]]
            if "." not in module:
                break
            module = module.rsplit(".", 1)[0]
        return []
