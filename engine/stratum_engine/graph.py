"""Graph algorithms over the file dependency graph.

Nodes are file paths; an edge a -> b means "a imports b".
"""

import re
from collections import deque

# Files that are run directly rather than imported, by name.
_ENTRY_NAMES = re.compile(
    r"(^|/)(__main__\.py|main\.(py|go|js|ts|mjs)|manage\.py|setup\.py|conftest\.py|"
    r"index\.(js|ts|jsx|tsx|mjs)|app\.(py|js|ts)|server\.(py|js|ts)|cli\.(py|js|ts))$"
)
_TEST_NAMES = re.compile(
    r"(^|/)(test_[^/]*\.py|[^/]*_test\.(py|go)|[^/]*\.(test|spec)\.(js|jsx|ts|tsx|mjs)|[^/]*Test\.java)$"
)
_CONFIG_NAMES = re.compile(r"(^|/)[^/]*\.config\.(js|ts|mjs|cjs)$")


def tarjan_scc(nodes, edges: dict) -> list[list[str]]:
    """Strongly connected components (iterative Tarjan, O(V + E)).

    An SCC is a maximal set of files that can all reach each other through
    imports; any SCC with more than one file is an import cycle. Iterative
    so that a long import chain can't overflow Python's recursion limit.
    """
    index_of, low, on_stack = {}, {}, set()
    stack, sccs = [], []
    counter = 0

    for start in nodes:
        if start in index_of:
            continue
        work = [(start, iter(sorted(edges.get(start, ()))))]
        index_of[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(edges.get(child, ())))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:  # node is the root of an SCC
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                sccs.append(sorted(comp))
    return sccs


def find_cycles(nodes, edges: dict) -> list[list[str]]:
    cycles = [c for c in tarjan_scc(nodes, edges) if len(c) > 1]
    return sorted(cycles, key=lambda c: (-len(c), c))


def detect_entry_points(files: dict, importers: dict) -> list[str]:
    """Files a program or test runner starts from."""
    entries = []
    for path, info in files.items():
        if (
            info.get("is_entry")
            or _ENTRY_NAMES.search(path)
            or _TEST_NAMES.search(path)
            or _CONFIG_NAMES.search(path)
            or "/test/" in f"/{path}"
            or "/tests/" in f"/{path}"
        ):
            entries.append(path)
    return sorted(entries)


def reachable_from(starts, edges: dict) -> set[str]:
    seen = set(starts)
    queue = deque(starts)
    while queue:
        node = queue.popleft()
        for nxt in edges.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def unreachable_files(files: dict, edges: dict, entries: list[str]) -> list[str]:
    """Files no entry point can reach through imports: likely dead code.

    Only meaningful when entry points were found; returns [] otherwise.
    Two language rules keep this from flagging live files:
      * Go compiles a package's files together, so every file in a reachable
        package directory is reachable.
      * Python runs a package's __init__.py whenever anything inside the
        package is imported, so it is reachable if any file below it is.
    """
    if not entries:
        return []
    alive = reachable_from(entries, edges)
    alive_go_dirs = {_dirname(p) for p in alive if p.endswith(".go")}
    alive_py_dirs = set()
    for p in alive:
        if p.endswith(".py"):
            d = _dirname(p)
            while d:
                alive_py_dirs.add(d)
                d = _dirname(d)
    dead = []
    for path, info in files.items():
        if path in alive:
            continue
        if info["language"] == "go" and _dirname(path) in alive_go_dirs:
            continue
        if path.endswith("/__init__.py") and _dirname(path) in alive_py_dirs:
            continue
        dead.append(path)
    return sorted(dead)


def _dirname(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""
