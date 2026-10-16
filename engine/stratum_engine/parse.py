import tree_sitter_python as tspython
from tree_sitter import Language, Parser

PYTHON = Language(tspython.language())
_parser = Parser(PYTHON)


def parse_python(source: bytes) -> dict:
    tree = _parser.parse(source)
    imports, functions, classes = [], [], []

    for node in tree.root_node.children:
        if node.type == "import_statement":
            for name in node.children_by_field_name("name"):
                imports.append(name.text.decode())
        elif node.type == "import_from_statement":
            module = node.child_by_field_name("module_name")
            imports.append(module.text.decode())
        elif node.type == "function_definition":
            functions.append(node.child_by_field_name("name").text.decode())
        elif node.type == "class_definition":
            classes.append(node.child_by_field_name("name").text.decode())

    return {"imports": imports, "functions": functions, "classes": classes}