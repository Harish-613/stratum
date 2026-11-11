import io
import json
import time

from stratum_engine.analyze import analyze
from stratum_engine.server import serve
from stratum_engine.session import Session


def write(root, files):
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


BASE = {
    "main.py": "import a\nif __name__ == '__main__':\n    pass\n",
    "a.py": "import b\n",
    "b.py": "x = 1\n",
    "c.py": "",
}


def strip(report):
    """Fields that should match between incremental and from-scratch analysis."""
    return {k: report[k] for k in ("files", "cycles", "unreachable", "duplicates", "complex_functions", "stats")}


def test_update_matches_full_rescan_after_edit(tmp_path):
    write(tmp_path, BASE)
    s = Session(str(tmp_path))
    s.scan()
    (tmp_path / "b.py").write_text("import a\n")  # creates cycle a <-> b
    r = s.update(["b.py"])
    assert r["changed"] == ["b.py"]
    assert r["cycles"] == [["a.py", "b.py"]]
    assert strip(r) == strip(analyze(str(tmp_path)))
    assert s.last_timings["full_resolve"] is False


def test_unchanged_save_is_skipped(tmp_path):
    write(tmp_path, BASE)
    s = Session(str(tmp_path))
    s.scan()
    (tmp_path / "a.py").write_text("import b\n")  # same content
    assert s.update(["a.py"])["changed"] == []


def test_new_and_deleted_files(tmp_path):
    write(tmp_path, BASE)
    s = Session(str(tmp_path))
    s.scan()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text("")
    (tmp_path / "pkg" / "d.py").write_text("")
    (tmp_path / "c.py").write_text("import pkg.d\n")
    r = s.update([str(tmp_path / "pkg/__init__.py"), str(tmp_path / "pkg/d.py"), "c.py"])  # absolute + relative
    assert r["files"]["c.py"]["imports"] == ["pkg/d.py"]
    assert s.last_timings["full_resolve"] is True
    (tmp_path / "b.py").unlink()
    r = s.update(["b.py"])
    assert "b.py" not in r["files"]
    assert r["files"]["a.py"]["imports"] == []
    assert strip(r) == strip(analyze(str(tmp_path)))


def test_new_file_fixes_previously_external_import(tmp_path):
    write(tmp_path, {"main.py": "import helpers\n"})
    s = Session(str(tmp_path))
    assert s.scan()["files"]["main.py"]["external_imports"] == ["helpers"]
    (tmp_path / "helpers.py").write_text("")
    r = s.update(["helpers.py"])
    assert r["files"]["main.py"]["imports"] == ["helpers.py"]


def test_ignores_unrelated_and_outside_paths(tmp_path):
    write(tmp_path, BASE)
    s = Session(str(tmp_path))
    s.scan()
    (tmp_path / "notes.md").write_text("hi")
    assert s.update(["notes.md", "/somewhere/else.py", "node_modules/x.js"])["changed"] == []


def test_server_protocol(tmp_path):
    write(tmp_path, BASE)
    requests = [
        {"id": 1, "method": "open", "params": {"root": str(tmp_path)}},
        {"id": 2, "method": "update", "params": {"paths": ["c.py"]}},
        {"id": 3, "method": "dot"},
        {"id": 4, "method": "bogus"},
        {"id": 5, "method": "shutdown"},
        {"id": 6, "method": "report"},  # never reached
    ]
    out = io.StringIO()
    serve(io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"), out)
    replies = [json.loads(l) for l in out.getvalue().splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4, 5]
    assert replies[0]["result"]["stats"]["files"] == 4
    assert "total_s" in replies[0]["result"]["timings"]
    assert replies[2]["result"]["dot"].startswith("digraph")
    assert "unknown method" in replies[3]["error"]


def test_server_requires_open_first():
    out = io.StringIO()
    serve(io.StringIO('{"id": 1, "method": "report"}\n'), out)
    assert "call 'open'" in json.loads(out.getvalue())["error"]
