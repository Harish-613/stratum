"""Extract imports, functions and classes from source files with Tree-sitter.

parse_file() returns a dict like:
    {
      "language": "python",
      "package": None,                      # Java package / Go package name
      "imports": [{"module": "os", "names": [], "line": 1}, ...],
      "functions": [{"name": "load", "line": 9, "end_line": 10}, ...],
      "classes": [{"name": "Graph", "line": 6, "end_line": 7}, ...],
    }

Each import's "module" is the raw text written in the source (".utils",
"./api", "fmt", "java.util.List"). Turning that into a file in the repo is
resolve.py's job.
"""

from stratum_engine.languages import family, parser_for


def parse_python(source: bytes) -> dict:
    """Original Python-only summary: plain lists of names, top-level only."""
    tree = parser_for("python").parse(source)
    imports, functions, classes = [], [], []

    for node in tree.root_node.children:
        if node.type == "import_statement":
            for name in node.children_by_field_name("name"):
                if name.type == "aliased_import":
                    name = name.child_by_field_name("name")
                imports.append(name.text.decode())
        elif node.type == "import_from_statement":
            module = node.child_by_field_name("module_name")
            imports.append(module.text.decode())
        elif node.type == "function_definition":
            functions.append(node.child_by_field_name("name").text.decode())
        elif node.type == "class_definition":
            classes.append(node.child_by_field_name("name").text.decode())

    return {"imports": imports, "functions": functions, "classes": classes}


# Node types that define a function or a class, per language family.
FUNCTION_NODES = {
    "python": {"function_definition"},
    "js": {"function_declaration", "generator_function_declaration", "method_definition"},
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration", "constructor_declaration"},
}
CLASS_NODES = {
    "python": {"class_definition"},
    "js": {"class_declaration", "abstract_class_declaration", "interface_declaration"},
    "go": set(),  # Go types are handled through type_spec below
    "java": {"class_declaration", "interface_declaration", "enum_declaration", "record_declaration"},
}


def parse_file(source: bytes, language: str) -> dict:
    tree = parser_for(language).parse(source)
    fam = family(language)
    result = {
        "language": language,
        "package": None,
        "imports": [],
        "functions": [],
        "classes": [],
        "is_entry": False,  # has a program entry point (main guard, main method, package main)
        "loc": sum(1 for line in source.splitlines() if line.strip()),
    }
    _walk(tree.root_node, fam, result)
    if fam == "go" and result["package"] == "main":
        result["is_entry"] = True
    return result


def _text(node) -> str:
    return node.text.decode(errors="replace") if node is not None else ""


def _span(node, name: str) -> dict:
    return {"name": name, "line": node.start_point[0] + 1, "end_line": node.end_point[0] + 1}


def _string_value(node) -> str:
    """Contents of a string literal node without its quotes."""
    raw = _text(node)
    return raw[1:-1] if len(raw) >= 2 and raw[0] in "'\"`" else raw


def _walk(root, fam: str, out: dict) -> None:
    # Iterative pre-order traversal: deep files would overflow Python's recursion limit.
    stack = [root]
    while stack:
        node = stack.pop()
        t = node.type

        if t in FUNCTION_NODES[fam]:
            name = node.child_by_field_name("name")
            if name is not None:
                out["functions"].append(_span(node, _text(name)))
                if fam == "java" and _text(name) == "main" and "static" in _text(node).split("(", 1)[0]:
                    out["is_entry"] = True  # public static void main(String[] args)
        elif t in CLASS_NODES[fam]:
            name = node.child_by_field_name("name")
            if name is not None:
                out["classes"].append(_span(node, _text(name)))
        else:
            _extract_extra(node, fam, out)

        stack.extend(reversed(node.children))


def _extract_extra(node, fam: str, out: dict) -> None:
    """Imports and language-specific definitions."""
    t = node.type
    line = node.start_point[0] + 1

    if fam == "python":
        if t == "if_statement" and node.parent is not None and node.parent.type == "module":
            cond = _text(node.child_by_field_name("condition"))
            if "__name__" in cond and "__main__" in cond:
                out["is_entry"] = True
        elif t == "import_statement":
            for name in node.children_by_field_name("name"):
                if name.type == "aliased_import":
                    name = name.child_by_field_name("name")
                out["imports"].append({"module": _text(name), "names": [], "line": line})
        elif t == "import_from_statement":
            module = _text(node.child_by_field_name("module_name"))
            names = []
            for n in node.children_by_field_name("name"):
                if n.type == "aliased_import":
                    n = n.child_by_field_name("name")
                names.append(_text(n))
            out["imports"].append({"module": module, "names": names, "line": line})

    elif fam == "js":
        if t in ("import_statement", "export_statement"):
            src = node.child_by_field_name("source")
            if src is not None:
                out["imports"].append({"module": _string_value(src), "names": [], "line": line})
        elif t == "call_expression":
            fn = node.child_by_field_name("function")
            args = node.child_by_field_name("arguments")
            is_require = fn is not None and fn.type == "identifier" and _text(fn) == "require"
            is_dynamic = fn is not None and fn.type == "import"
            if (is_require or is_dynamic) and args is not None:
                strings = [c for c in args.named_children if c.type == "string"]
                if strings:
                    out["imports"].append({"module": _string_value(strings[0]), "names": [], "line": line})
        elif t == "variable_declarator":
            # const handler = () => {...}  /  const f = function() {...}
            value = node.child_by_field_name("value")
            name = node.child_by_field_name("name")
            if value is not None and name is not None and value.type in ("arrow_function", "function_expression", "function"):
                out["functions"].append(_span(value, _text(name)))

    elif fam == "go":
        if t == "package_clause":
            for c in node.named_children:
                if c.type == "package_identifier":
                    out["package"] = _text(c)
        elif t == "import_spec":
            path = node.child_by_field_name("path")
            out["imports"].append({"module": _string_value(path), "names": [], "line": line})
        elif t == "type_spec":
            name = node.child_by_field_name("name")
            typ = node.child_by_field_name("type")
            if name is not None and typ is not None and typ.type in ("struct_type", "interface_type"):
                out["classes"].append(_span(node, _text(name)))

    elif fam == "java":
        if t == "package_declaration":
            for c in node.named_children:
                if c.type in ("scoped_identifier", "identifier"):
                    out["package"] = _text(c)
        elif t == "import_declaration":
            target = next((c for c in node.named_children if c.type in ("scoped_identifier", "identifier")), None)
            if target is not None:
                module = _text(target)
                is_static = any(c.type == "static" for c in node.children)
                wildcard = any(c.type == "asterisk" for c in node.children)
                names = ["*"] if wildcard else []
                if is_static and not wildcard:
                    module = module.rsplit(".", 1)[0]  # import static a.B.method -> class a.B
                out["imports"].append({"module": module, "names": names, "line": line})
