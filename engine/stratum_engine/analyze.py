"""Whole-repo analysis: parse every file, build the import graph, and report
cycles, unreachable files and coupling."""

from collections import Counter, defaultdict

from stratum_engine.graph import detect_entry_points, find_cycles, unreachable_files
from stratum_engine.resolve import Resolver
from stratum_engine.scan import scan_all


def build_graph(root: str, files: dict):
    """Returns (edges, external) where edges[a] = set of repo files a imports
    and external[a] = sorted list of imports that left the repo."""
    resolver = Resolver(root, files)
    edges = {path: set() for path in files}
    external = {}
    for path, info in files.items():
        ext = set()
        for imp in info["imports"]:
            if imp.get("type_only"):
                continue  # erased before runtime: not a real dependency
            targets = resolver.resolve(path, imp)
            if targets:
                edges[path].update(targets)
            else:
                ext.add(imp["module"])
        external[path] = sorted(ext)
    return edges, external


# Functions smaller than this many syntax nodes are too trivial to call duplicates
# (one-line getters, empty constructors, `return x`).
MIN_DUPLICATE_SIZE = 40
COMPLEXITY_THRESHOLD = 10  # common rule of thumb for "should be split up"


def find_duplicates(files: dict, min_size: int = MIN_DUPLICATE_SIZE) -> list[dict]:
    groups = defaultdict(list)
    for path, info in files.items():
        for fn in info["functions"]:
            if fn.get("size", 0) >= min_size:
                groups[fn["hash"]].append((path, fn))
    dupes = []
    for h, members in groups.items():
        if len(members) < 2:
            continue
        lines = max(fn["end_line"] - fn["line"] + 1 for _, fn in members)
        dupes.append({
            "hash": h,
            "size": members[0][1]["size"],
            "lines": lines,
            "instances": [
                {"file": p, "name": fn["name"], "line": fn["line"], "end_line": fn["end_line"]}
                for p, fn in sorted(members, key=lambda m: (m[0], m[1]["line"]))
            ],
        })
    # Biggest payoff first: most duplicated lines.
    return sorted(dupes, key=lambda d: (-d["lines"] * (len(d["instances"]) - 1), d["hash"]))


def complex_functions(files: dict, threshold: int = COMPLEXITY_THRESHOLD) -> list[dict]:
    found = [
        {"file": p, "name": fn["name"], "line": fn["line"], "complexity": fn["complexity"]}
        for p, info in files.items()
        for fn in info["functions"]
        if fn.get("complexity", 1) >= threshold
    ]
    return sorted(found, key=lambda f: (-f["complexity"], f["file"], f["line"]))


def analyze_files(root: str, files: dict) -> dict:
    edges, external = build_graph(root, files)
    return analyze_from_graph(root, files, edges, external)


def analyze_from_graph(root: str, files: dict, edges: dict, external: dict) -> dict:
    """Graph-wide passes over an already-resolved graph (used by Session for incremental updates)."""
    importers = defaultdict(set)
    for src, dsts in edges.items():
        for d in dsts:
            importers[d].add(src)

    entries = detect_entry_points(files, importers)
    cycles = find_cycles(files.keys(), edges)
    dead = unreachable_files(files, edges, entries)
    dupes = find_duplicates(files)
    complex_fns = complex_functions(files)

    file_report = {
        path: {
            "language": info["language"],
            "loc": info["loc"],
            "functions": len(info["functions"]),
            "classes": len(info["classes"]),
            "imports": sorted(edges[path]),
            "external_imports": external[path],
            "fan_in": len(importers[path]),
            "fan_out": len(edges[path]),
            "max_complexity": max((fn["complexity"] for fn in info["functions"]), default=0),
        }
        for path, info in files.items()
    }
    return {
        "root": root,
        "stats": {
            "files": len(files),
            "loc": sum(i["loc"] for i in files.values()),
            "languages": dict(Counter(i["language"] for i in files.values()).most_common()),
            "edges": sum(len(d) for d in edges.values()),
            "cycles": len(cycles),
            "unreachable": len(dead),
            "duplicate_groups": len(dupes),
            "complex_functions": len(complex_fns),
        },
        "files": file_report,
        "cycles": cycles,
        "entry_points": entries,
        "unreachable": dead,
        "duplicates": dupes,
        "complex_functions": complex_fns,
    }


def analyze(root: str) -> dict:
    return analyze_files(root, scan_all(root))


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _dir_of(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def _folder_key(path: str, depth: int) -> str:
    parts = path.split("/")[:-1]
    return "/".join(parts[:depth]) if parts else "(root)"


MAX_FOCUS_IMPORTERS = 12

_HEADER = [
    '  graph [rankdir=LR, fontname="Helvetica", fontsize=10, bgcolor="transparent"];',
    '  node [shape=box, style="rounded,filled", fillcolor="#eef2f7", fontname="Helvetica", fontsize=9];',
    '  edge [color="#8a94a6", arrowsize=0.6];',
]


def to_dot(report: dict, focus: str = "") -> str:
    """Graphviz DOT of the file graph. Folders become clusters, files in a
    cycle are red, unreachable files are dashed.

    focus: only draw files under this folder, plus (greyed out) the files they
    import or are imported by, so a big repo can be explored one area at a time.
    Node ids are "file:<path>" so the extension can open the file on click.
    """
    cycle_of = {f: i for i, c in enumerate(report["cycles"]) for f in c}
    unreachable = set(report["unreachable"])
    files = report["files"]
    prefix = focus.rstrip("/") + "/" if focus else ""
    core = {p for p in files if p.startswith(prefix)}
    neighbours = set()
    if focus:
        for p in core:
            neighbours.update(files[p]["imports"])
        importers = {p for p, info in files.items() if p not in core and any(d in core for d in info["imports"])}
        if len(importers) <= MAX_FOCUS_IMPORTERS:
            neighbours |= importers
            importers = set()
        neighbours -= core
    else:
        importers = set()
    shown = core | neighbours

    by_dir = defaultdict(list)
    for p in shown:
        by_dir[_dir_of(p)].append(p)

    lines = ["digraph stratum {", *_HEADER]
    for i, (directory, paths) in enumerate(sorted(by_dir.items())):
        indent = "  "
        if directory:
            lines.append(f'  subgraph cluster_{i} {{ label="{_esc(directory)}"; style="rounded"; color="#c5ccd8";')
            indent = "    "
        for p in sorted(paths):
            attrs = [f'label="{_esc(p.rsplit("/", 1)[-1])}"', f'id="file:{_esc(p)}"', f'tooltip="{_esc(p)}"']
            if p in cycle_of:
                attrs.append('fillcolor="#fde2e2", color="#d64545"')
            if p in unreachable:
                attrs.append('fontcolor="#9aa3b2", style="rounded,dashed,filled"')
            if p in neighbours:
                attrs.append('fillcolor="#f7f8fa", fontcolor="#9aa3b2", color="#d5d9e0"')
            lines.append(f'{indent}"{_esc(p)}" [{", ".join(attrs)}];')
        if directory:
            lines.append("  }")
    if importers:
        # Too many outside importers to draw one by one: summarize them as one node.
        lines.append(f'  "__importers__" [label="{len(importers)} other files\\nimport this folder", '
                     'shape=note, fillcolor="#f7f8fa", fontcolor="#9aa3b2", color="#d5d9e0"];')
        targets = sorted({d for p in importers for d in files[p]["imports"] if d in core})
        for d in targets:
            lines.append(f'  "__importers__" -> "{_esc(d)}" [style=dashed, color="#c5ccd8"];')
    for src in sorted(shown):
        for dst in files[src]["imports"]:
            if dst not in shown or (src in neighbours and dst in neighbours):
                continue
            same_cycle = src in cycle_of and cycle_of.get(dst) == cycle_of[src]
            red = ' [color="#d64545", penwidth=1.4]' if same_cycle else ""
            lines.append(f'  "{_esc(src)}" -> "{_esc(dst)}"{red};')
    lines.append("}")
    return "\n".join(lines)


def to_folder_dot(report: dict, depth: int = 2) -> str:
    """Graphviz DOT with one node per folder (truncated to `depth` levels).
    Edge labels count the file-level imports between two folders; folders
    that contain a cycle are red. Node ids are "dir:<folder>"."""
    files = report["files"]
    sizes, cyc = defaultdict(int), set()
    for c in report["cycles"]:
        for f in c:
            cyc.add(_folder_key(f, depth))
    for p in files:
        sizes[_folder_key(p, depth)] += 1
    weights = defaultdict(int)
    for src, info in files.items():
        a = _folder_key(src, depth)
        for dst in info["imports"]:
            b = _folder_key(dst, depth)
            if a != b:
                weights[(a, b)] += 1

    lines = ["digraph stratum {", *_HEADER]
    for folder in sorted(sizes):
        n = sizes[folder]
        color = ', fillcolor="#fde2e2", color="#d64545"' if folder in cyc else ""
        lines.append(f'  "{_esc(folder)}" [label="{_esc(folder)}\\n{n} file{"s" if n != 1 else ""}", id="dir:{_esc(folder)}"{color}];')
    for (a, b), w in sorted(weights.items()):
        width = min(1 + w / 10, 4)
        lines.append(f'  "{_esc(a)}" -> "{_esc(b)}" [label="{w}", fontsize=8, penwidth={width:.1f}];')
    lines.append("}")
    return "\n".join(lines)
