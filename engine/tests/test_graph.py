import random

from stratum_engine.analyze import analyze, to_dot
from stratum_engine.graph import find_cycles, reachable_from, tarjan_scc


def write(root, files: dict):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


# ---------- Tarjan ----------

def test_tarjan_finds_cycles_and_singletons():
    edges = {"a": {"b"}, "b": {"c"}, "c": {"a"}, "d": {"a"}, "e": set()}
    sccs = tarjan_scc(["a", "b", "c", "d", "e"], edges)
    assert sorted(sccs) == [["a", "b", "c"], ["d"], ["e"]]
    assert find_cycles(["a", "b", "c", "d", "e"], edges) == [["a", "b", "c"]]


def test_tarjan_two_separate_cycles_sorted_by_size():
    edges = {"a": {"b"}, "b": {"a"}, "x": {"y"}, "y": {"z"}, "z": {"x"}}
    assert find_cycles(edges.keys(), edges) == [["x", "y", "z"], ["a", "b"]]


def test_tarjan_handles_long_chain_without_recursion_error():
    n = 20000
    edges = {str(i): {str(i + 1)} for i in range(n)}
    edges[str(n)] = {"0"}  # one giant cycle
    cycles = find_cycles(edges.keys(), edges)
    assert len(cycles) == 1 and len(cycles[0]) == n + 1


def test_tarjan_matches_brute_force_reachability():
    rng = random.Random(3)
    for _ in range(50):
        nodes = [str(i) for i in range(25)]
        edges = {n: {rng.choice(nodes) for _ in range(rng.randint(0, 3))} for n in nodes}
        reach = {n: reachable_from([n], edges) for n in nodes}
        expected = {frozenset(m for m in nodes if m in reach[n] and n in reach[m]) for n in nodes}
        got = {frozenset(c) for c in tarjan_scc(nodes, edges)}
        assert got == expected


# ---------- resolution, per language ----------

def test_python_absolute_relative_and_package_imports(tmp_path):
    write(tmp_path, {
        "main.py": "from app import service\nimport app.models\nif __name__ == '__main__':\n    pass\n",
        "app/__init__.py": "",
        "app/service.py": "from .models import User\nfrom . import util\nimport requests\n",
        "app/models.py": "from ..lib.db import connect\n",
        "app/util.py": "",
        "lib/db.py": "",
        "app/legacy/__init__.py": "",
        "app/legacy/old.py": "",
    })
    r = analyze(str(tmp_path))
    f = r["files"]
    assert f["main.py"]["imports"] == ["app/models.py", "app/service.py"]
    assert f["app/service.py"]["imports"] == ["app/models.py", "app/util.py"]
    assert f["app/service.py"]["external_imports"] == ["requests"]
    assert f["app/models.py"]["imports"] == ["lib/db.py"]
    # app/__init__.py is never named in an import, but Python runs it whenever
    # app.service or app.models is imported, so it must not be flagged as dead.
    assert r["unreachable"] == ["app/legacy/__init__.py", "app/legacy/old.py"]


def test_python_src_layout(tmp_path):
    write(tmp_path, {
        "src/mypkg/core.py": "from mypkg.helpers import h\n",
        "src/mypkg/helpers.py": "",
    })
    r = analyze(str(tmp_path))
    assert r["files"]["src/mypkg/core.py"]["imports"] == ["src/mypkg/helpers.py"]


def test_js_ts_relative_imports_extensions_and_index(tmp_path):
    write(tmp_path, {
        "src/index.ts": "import { a } from './a';\nimport b from './lib';\nimport c from './c.js';\nimport React from 'react';\n",
        "src/a.tsx": "export const a = 1;\n",
        "src/lib/index.ts": "const u = require('../util');\n",
        "src/c.ts": "",
        "src/util.js": "",
    })
    f = analyze(str(tmp_path))["files"]
    assert f["src/index.ts"]["imports"] == ["src/a.tsx", "src/c.ts", "src/lib/index.ts"]
    assert f["src/index.ts"]["external_imports"] == ["react"]
    assert f["src/lib/index.ts"]["imports"] == ["src/util.js"]


def test_go_module_packages(tmp_path):
    write(tmp_path, {
        "go.mod": "module example.com/shop\n\ngo 1.22\n",
        "cmd/server/main.go": 'package main\nimport (\n\t"fmt"\n\t"example.com/shop/internal/store"\n)\n',
        "internal/store/store.go": "package store\n",
        "internal/store/cache.go": "package store\n",
        "internal/store/store_test.go": "package store\n",
        "internal/unused/x.go": "package unused\n",
    })
    r = analyze(str(tmp_path))
    assert r["files"]["cmd/server/main.go"]["imports"] == ["internal/store/cache.go", "internal/store/store.go"]
    assert r["files"]["cmd/server/main.go"]["external_imports"] == ["fmt"]
    assert r["unreachable"] == ["internal/unused/x.go"]


def test_java_classes_wildcards_and_main(tmp_path):
    write(tmp_path, {
        "src/com/acme/App.java": "package com.acme;\nimport com.acme.model.User;\nimport com.acme.util.*;\npublic class App { public static void main(String[] a) {} }\n",
        "src/com/acme/model/User.java": "package com.acme.model;\npublic class User {}\n",
        "src/com/acme/util/Strings.java": "package com.acme.util;\npublic class Strings {}\n",
        "src/com/acme/util/Nums.java": "package com.acme.util;\npublic class Nums {}\n",
        "src/com/acme/Legacy.java": "package com.acme;\npublic class Legacy {}\n",
    })
    r = analyze(str(tmp_path))
    assert r["files"]["src/com/acme/App.java"]["imports"] == [
        "src/com/acme/model/User.java", "src/com/acme/util/Nums.java", "src/com/acme/util/Strings.java"]
    assert r["entry_points"] == ["src/com/acme/App.java"]
    assert r["unreachable"] == ["src/com/acme/Legacy.java"]


# ---------- whole report ----------

def test_cycle_report_fan_in_out_and_dot(tmp_path):
    write(tmp_path, {
        "main.py": "import a\nif __name__ == '__main__':\n    pass\n",
        "a.py": "import b\n",
        "b.py": "import c\n",
        "c.py": "import a\n",
    })
    r = analyze(str(tmp_path))
    assert r["cycles"] == [["a.py", "b.py", "c.py"]]
    assert r["files"]["a.py"]["fan_in"] == 2
    assert r["files"]["a.py"]["fan_out"] == 1
    assert r["stats"]["files"] == 4 and r["stats"]["cycles"] == 1
    dot = to_dot(r)
    assert dot.startswith("digraph stratum {")
    assert '"c.py" -> "a.py" [color="#d64545"' in dot
    assert '"main.py" -> "a.py";' in dot


def test_no_entry_points_means_no_dead_code_claims(tmp_path):
    write(tmp_path, {"lib/a.py": "import b\n", "lib/b.py": ""})
    r = analyze(str(tmp_path))
    assert r["entry_points"] == []
    assert r["unreachable"] == []


def test_python_package_boundaries_beat_folder_proximity(tmp_path):
    # Mirrors Flask's repo: tests/apps/inner/flask.py lives inside a package,
    # so "import flask" from a test must resolve to src/flask, not the fixture.
    write(tmp_path, {
        "src/flask/__init__.py": "",
        "tests/__init__.py": "",
        "tests/apps/__init__.py": "",
        "tests/apps/inner/__init__.py": "",
        "tests/apps/inner/flask.py": "",
        "tests/test_basic.py": "import flask\nfrom tests.apps.inner import flask as fixture\n",
    })
    f = analyze(str(tmp_path))["files"]
    assert f["tests/test_basic.py"]["imports"] == ["src/flask/__init__.py", "tests/apps/inner/flask.py"]
