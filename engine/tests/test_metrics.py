from stratum_engine.analyze import analyze, find_duplicates
from stratum_engine.parse import parse_file


def fn(src: bytes, language: str, index: int = 0) -> dict:
    return parse_file(src, language)["functions"][index]


# ---------- cyclomatic complexity ----------

def test_straight_line_function_has_complexity_one():
    assert fn(b"def f(x):\n    y = x + 1\n    return y\n", "python")["complexity"] == 1


def test_python_branches_loops_and_boolean_ops():
    src = b"""
def f(xs, a, b):
    for x in xs:            # +1
        if x and a:         # +1 if, +1 and
            pass
        elif x or b:        # +1 elif, +1 or
            pass
    try:
        pass
    except ValueError:      # +1
        pass
    return [y for y in xs if y]   # +1 for, +1 if
"""
    assert fn(src, "python")["complexity"] == 1 + 8


def test_nested_def_is_scored_separately():
    src = b"def outer(x):\n    def inner(y):\n        if y:\n            return 1\n    return inner(x)\n"
    funcs = parse_file(src, "python")["functions"]
    assert [f["name"] for f in funcs] == ["outer", "inner"]
    assert funcs[0]["complexity"] == 1   # inner's `if` doesn't count against outer
    assert funcs[1]["complexity"] == 2


def test_js_callbacks_count_toward_enclosing_function():
    src = b"function f(xs) { return xs.filter(x => x > 0 ? true : x === null || x === 1); }"
    assert fn(src, "javascript")["complexity"] == 1 + 2  # ternary + ||


def test_go_and_java_complexity():
    go = b"package m\nfunc f(x int) int {\n if x > 0 && x < 9 { return 1 }\n for i := 0; i < x; i++ {}\n switch x { case 1: return 2\n case 2: return 3 }\n return 0\n}\n"
    assert fn(go, "go")["complexity"] == 1 + 5  # if, &&, for, 2 cases
    java = b"class A { int f(int x) { if (x > 0) { return 1; } try { } catch (Exception e) { } return x > 1 ? 2 : 3; } }"
    assert fn(java, "java")["complexity"] == 1 + 3  # if, catch, ternary


# ---------- structural hashing ----------

BODY_A = b"""
def total_price(items, tax_rate):
    subtotal = 0
    for item in items:
        if item.quantity > 0:
            subtotal += item.price * item.quantity
    discount = 0.1 if subtotal > 100 else 0
    return round(subtotal * (1 - discount) * (1 + tax_rate), 2)
"""
# Same logic, every name and constant changed.
BODY_B = b"""
def compute_cost(rows, vat):
    acc = 0
    for r in rows:
        if r.count > 5:
            acc += r.cost * r.count
    off = 0.25 if acc > 999 else 0
    return round(acc * (1 - off) * (1 + vat), 4)
"""
# Same names, different structure (a while loop).
BODY_C = b"""
def total_price(items, tax_rate):
    subtotal = 0
    i = 0
    while i < len(items):
        subtotal += items[i].price
        i += 1
    return round(subtotal * (1 + tax_rate), 2)
"""


def test_renamed_copy_has_same_hash():
    assert fn(BODY_A, "python")["hash"] == fn(BODY_B, "python")["hash"]


def test_different_structure_has_different_hash():
    assert fn(BODY_A, "python")["hash"] != fn(BODY_C, "python")["hash"]


def test_comments_and_formatting_do_not_change_hash():
    noisy = BODY_A.replace(b"    subtotal = 0\n", b"    # running total\n    subtotal   =   0\n\n")
    assert fn(BODY_A, "python")["hash"] == fn(noisy, "python")["hash"]


def test_different_callbacks_are_not_duplicates():
    a = b"function f(xs) { return xs.map(x => x * 2).filter(x => x > 10).slice(0, 5).join(','); }"
    b = b"function f(xs) { return xs.map(x => { if (x) { return x; } return 0; }).filter(x => x > 10).slice(0, 5).join(','); }"
    assert fn(a, "javascript")["hash"] != fn(b, "javascript")["hash"]


def test_duplicates_report_groups_across_files_and_skips_tiny(tmp_path):
    (tmp_path / "billing.py").write_bytes(BODY_A + b"\ndef get_id(self):\n    return self.id\n")
    (tmp_path / "shop").mkdir()
    (tmp_path / "shop" / "cart.py").write_bytes(BODY_B + b"\ndef get_name(self):\n    return self.name\n")
    (tmp_path / "other.py").write_bytes(BODY_C)
    r = analyze(str(tmp_path))
    assert len(r["duplicates"]) == 1  # the one-line getters are too small to count
    group = r["duplicates"][0]
    assert [(i["file"], i["name"]) for i in group["instances"]] == [
        ("billing.py", "total_price"), ("shop/cart.py", "compute_cost")]
    assert group["lines"] == 7
    assert r["stats"]["duplicate_groups"] == 1


def test_complex_functions_report(tmp_path):
    branches = "".join(f"    if x == {i}:\n        return {i}\n" for i in range(12))
    (tmp_path / "rules.py").write_text(f"def classify(x):\n{branches}    return -1\n\ndef simple():\n    return 0\n")
    r = analyze(str(tmp_path))
    assert r["complex_functions"] == [{"file": "rules.py", "name": "classify", "line": 1, "complexity": 13}]
    assert r["files"]["rules.py"]["max_complexity"] == 13


def test_find_duplicates_min_size_threshold():
    files = {"a.py": {"functions": [{"name": "f", "hash": "h", "size": 10, "line": 1, "end_line": 2}]},
             "b.py": {"functions": [{"name": "g", "hash": "h", "size": 10, "line": 1, "end_line": 2}]}}
    assert find_duplicates(files, min_size=40) == []
    assert len(find_duplicates(files, min_size=5)) == 1
