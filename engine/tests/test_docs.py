import json

from stratum_engine.analyze import analyze
from stratum_engine.docs import CachedLLM, chunk_source, generate_docs, plan
from stratum_engine.parse import parse_file


def make_module(n_funcs: int, body_lines: int = 6) -> bytes:
    out = ["import os", "import sys", ""]
    for i in range(n_funcs):
        out.append(f"def func_{i}(x):")
        out += [f"    x = x + {j}  # step {j} of function {i}" for j in range(body_lines)]
        out += ["    return x", ""]
    return "\n".join(out).encode()


def test_small_file_is_one_chunk():
    src = make_module(2)
    chunks = chunk_source(src, "python", max_tokens=10_000)
    assert len(chunks) == 1
    assert chunks[0]["start_line"] == 1


def test_chunks_never_split_a_function_and_cover_every_function():
    src = make_module(30)
    funcs = parse_file(src, "python")["functions"]
    chunks = chunk_source(src, "python", max_tokens=200)
    assert len(chunks) > 3
    for fn in funcs:  # each function lies entirely inside exactly one chunk
        owners = [c for c in chunks if c["start_line"] <= fn["line"] and fn["end_line"] <= c["end_line"]]
        assert len(owners) == 1, fn
    assert all(c["tokens"] <= 200 * 1.2 for c in chunks)
    # chunks are in order and don't overlap
    assert all(a["end_line"] < b["start_line"] for a, b in zip(chunks, chunks[1:]))


def test_oversized_class_splits_into_methods_keeping_header():
    methods = "\n".join(
        f"    def m{i}(self):\n" + "\n".join(f"        self.v{j} = {j}  # filler filler filler" for j in range(8)) + "\n"
        for i in range(20))
    src = f"class Big(Base):\n{methods}\n".encode()
    chunks = chunk_source(src, "python", max_tokens=150)
    assert len(chunks) > 1
    assert chunks[0]["text"].startswith("class Big(Base):")
    assert all("def m" in c["text"] for c in chunks)


def test_go_and_typescript_chunk_at_declarations():
    go = "package m\n\n" + "\n".join(f"func F{i}() int {{\n\treturn {i} // filler filler filler filler filler\n}}\n" for i in range(40))
    chunks = chunk_source(go.encode(), "go", max_tokens=120)
    assert len(chunks) > 2 and all(c["text"].lstrip().startswith(("package", "func")) for c in chunks)
    ts = "\n".join(f"export function f{i}(): number {{\n  return {i}; // filler filler filler filler\n}}\n" for i in range(40))
    chunks = chunk_source(ts.encode(), "typescript", max_tokens=120)
    assert len(chunks) > 2 and all(c["text"].lstrip().startswith("export function") for c in chunks)


def test_pipeline_with_fake_llm_writes_docs_and_caches(tmp_path):
    (tmp_path / "app.py").write_bytes(b"import util\nif __name__ == '__main__':\n    util.run()\n")
    (tmp_path / "util.py").write_bytes(make_module(30))
    report = analyze(str(tmp_path))
    prompts = []

    def fake_llm(system, prompt):
        prompts.append(prompt)
        return f"- summary #{len(prompts)}"

    result = generate_docs(str(tmp_path), report, fake_llm, max_tokens=200)
    assert ".stratum/docs/ARCHITECTURE.md" in result["written"]
    assert (tmp_path / ".stratum/docs/util.py.md").read_text().startswith("# `util.py`")
    first_calls = result["llm_calls"]
    assert first_calls == len(prompts) > 3  # several util.py chunks + combine + app + architecture
    assert any("Most imported: util.py (1)" in p for p in prompts)  # graph facts reach the LLM

    # Second run: everything comes from the cache.
    again = generate_docs(str(tmp_path), report, fake_llm, max_tokens=200)
    assert again["llm_calls"] == 0 and again["cache_hits"] == first_calls

    # Edit one function: only its chunk, util's combine step and the architecture doc re-run.
    src = (tmp_path / "util.py").read_bytes().replace(b"def func_29(x):", b"def func_29(x, y=1):")
    (tmp_path / "util.py").write_bytes(src)
    third = generate_docs(str(tmp_path), analyze(str(tmp_path)), fake_llm, max_tokens=200)
    assert third["llm_calls"] == 3


def test_cache_file_round_trip(tmp_path):
    calls = []
    c = CachedLLM(lambda s, p: calls.append(p) or "ok", tmp_path / "c.json")
    c("sys", "a")
    c("sys", "a")
    c.save()
    assert len(calls) == 1
    assert len(json.loads((tmp_path / "c.json").read_text())) == 1


def test_dry_run_plan(tmp_path):
    (tmp_path / "util.py").write_bytes(make_module(30))
    p = plan(str(tmp_path), ["util.py", "README.md"], max_tokens=200)
    assert [f["file"] for f in p] == ["util.py"]
    assert len(p[0]["chunks"]) > 3 and "text" not in p[0]["chunks"][0]
