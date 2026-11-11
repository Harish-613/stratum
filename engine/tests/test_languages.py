from stratum_engine.languages import language_for
from stratum_engine.parse import parse_file
from stratum_engine.scan import scan_all


def modules(result):
    return [i["module"] for i in result["imports"]]


def names(items):
    return [i["name"] for i in items]


def test_language_detection():
    assert language_for("a/b.py") == "python"
    assert language_for("App.TSX") == "tsx"
    assert language_for("main.go") == "go"
    assert language_for("Foo.java") == "java"
    assert language_for("index.mjs") == "javascript"
    assert language_for("README.md") is None
    assert language_for("Makefile") is None


def test_python_relative_and_from_imports():
    src = b"from . import utils, models as m\nfrom ..core.graph import Graph\nimport os.path\n"
    r = parse_file(src, "python")
    assert modules(r) == [".", "..core.graph", "os.path"]
    assert r["imports"][0]["names"] == ["utils", "models"]
    assert r["imports"][1]["line"] == 2


def test_python_methods_and_nested_functions():
    src = b"class A:\n    def m(self):\n        def inner():\n            pass\n\ndef top():\n    pass\n"
    r = parse_file(src, "python")
    assert names(r["functions"]) == ["m", "inner", "top"]
    assert r["functions"][0]["line"] == 2
    assert r["functions"][0]["end_line"] == 4
    assert names(r["classes"]) == ["A"]


def test_javascript_imports_requires_and_functions():
    src = (
        b"import React from 'react';\n"
        b"export { a } from './a.js';\n"
        b"const b = require(\"./b\");\n"
        b"const lazy = () => import('./lazy');\n"
        b"function greet() {}\n"
        b"class Box { open() {} }\n"
        b"const add = function (x, y) { return x + y; };\n"
    )
    r = parse_file(src, "javascript")
    assert modules(r) == ["react", "./a.js", "./b", "./lazy"]
    assert names(r["functions"]) == ["lazy", "greet", "open", "add"]
    assert names(r["classes"]) == ["Box"]


def test_typescript_and_tsx():
    ts = parse_file(b"import type { T } from '../types';\ninterface Shape {}\nexport class Circle implements Shape {}\n", "typescript")
    assert modules(ts) == ["../types"]
    assert names(ts["classes"]) == ["Shape", "Circle"]

    tsx = parse_file(b"import { useState } from 'react';\nexport const App = () => <div />;\n", "tsx")
    assert modules(tsx) == ["react"]
    assert names(tsx["functions"]) == ["App"]


def test_go_imports_types_and_methods():
    src = b'package server\n\nimport (\n\tf "fmt"\n\t"example.com/app/db"\n)\n\ntype Server struct{}\ntype Store interface{}\ntype ID int\n\nfunc (s *Server) Start() {}\nfunc New() *Server { return nil }\n'
    r = parse_file(src, "go")
    assert r["package"] == "server"
    assert modules(r) == ["fmt", "example.com/app/db"]
    assert names(r["classes"]) == ["Server", "Store"]  # ID is not a struct/interface
    assert names(r["functions"]) == ["Start", "New"]


def test_java_package_imports_and_members():
    src = (
        b"package com.acme.app;\n"
        b"import java.util.List;\n"
        b"import com.acme.util.*;\n"
        b"import static com.acme.math.Ops.add;\n"
        b"public class Main {\n  public Main() {}\n  void run() {}\n  enum Mode { A }\n}\n"
    )
    r = parse_file(src, "java")
    assert r["package"] == "com.acme.app"
    assert modules(r) == ["java.util.List", "com.acme.util", "com.acme.math.Ops"]
    assert r["imports"][1]["names"] == ["*"]
    assert names(r["functions"]) == ["Main", "run"]
    assert names(r["classes"]) == ["Main", "Mode"]


def test_broken_source_still_parses():
    # Tree-sitter is error-tolerant: a half-typed file should not crash the scan.
    r = parse_file(b"def broken(:\n    pass\nimport os\n", "python")
    assert "os" in modules(r)


def test_scan_all_mixed_repo(tmp_path):
    (tmp_path / "api").mkdir()
    (tmp_path / "api" / "server.go").write_text('package api\nimport "fmt"\n')
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "app.ts").write_text("import { x } from './util';\n")
    (tmp_path / "tool.py").write_text("import json\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("module.exports = 1;\n")
    (tmp_path / "notes.txt").write_text("hi")

    result = scan_all(str(tmp_path))
    assert list(result) == ["api/server.go", "tool.py", "web/app.ts"]
    assert result["web/app.ts"]["language"] == "typescript"
