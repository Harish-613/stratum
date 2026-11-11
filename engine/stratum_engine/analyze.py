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


def to_dot(report: dict) -> str:
    """Graphviz DOT: folders become clusters, cycle edges are drawn in red."""
    cycle_of = {f: i for i, c in enumerate(report["cycles"]) for f in c}
    in_cycle = set(cycle_of)
    by_dir = defaultdict(list)
    for path in report["files"]:
        by_dir[path.rsplit("/", 1)[0] if "/" in path else ""].append(path)

    lines = [
        "digraph stratum {",
        '  graph [rankdir=LR, fontname="Helvetica", fontsize=10];',
        '  node [shape=box, style="rounded,filled", fillcolor="#eef2f7", fontname="Helvetica", fontsize=9];',
        '  edge [color="#8a94a6", arrowsize=0.6];',
    ]
    for i, (directory, paths) in enumerate(sorted(by_dir.items())):
        indent = "  "
        if directory:
            lines.append(f'  subgraph cluster_{i} {{ label="{directory}"; style="rounded"; color="#c5ccd8";')
            indent = "    "
        for p in sorted(paths):
            label = p.rsplit("/", 1)[-1]
            color = ', fillcolor="#fde2e2", color="#d64545"' if p in in_cycle else ""
            unreachable = ', fontcolor="#9aa3b2", style="rounded,dashed"' if p in report["unreachable"] else ""
            lines.append(f'{indent}"{p}" [label="{label}", id="{p}"{color}{unreachable}];')
        if directory:
            lines.append("  }")
    for src in sorted(report["files"]):
        for dst in report["files"][src]["imports"]:
            same_cycle = src in cycle_of and cycle_of.get(dst) == cycle_of[src]
            red = ' [color="#d64545", penwidth=1.4]' if same_cycle else ""
            lines.append(f'  "{src}" -> "{dst}"{red};')
    lines.append("}")
    return "\n".join(lines)
