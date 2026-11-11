"""Per-function metrics computed from the Tree-sitter syntax tree.

structural_hash(): a fingerprint of a function's AST with every identifier
replaced by ID and every literal by LIT. Two functions that are the same code
with renamed variables or different constants get the same hash, which is how
Stratum finds copy-pasted logic ("type-2 clones") that plain text diffing misses.

cyclomatic_complexity(): 1 + the number of decision points (if, loop, case,
catch, ternary, && / ||). Roughly the number of independent paths through the
function, so higher means harder to test and reason about.

Nested *named* definitions (an inner def, a method of a local class) are left
out of both: they are reported as functions of their own. Anonymous callbacks
(lambdas, arrow functions, Go func literals) stay in, because they are part of
the enclosing function's logic.
"""

import hashlib

IDENTIFIER_TYPES = {
    "identifier", "property_identifier", "field_identifier", "type_identifier",
    "package_identifier", "shorthand_property_identifier", "shorthand_property_identifier_pattern",
    "private_property_identifier", "statement_identifier",
}
LITERAL_TYPES = {
    "string", "string_fragment", "string_content", "number", "integer", "float", "true", "false",
    "none", "null", "undefined", "template_string", "interpreted_string_literal", "raw_string_literal",
    "interpreted_string_literal_content", "rune_literal", "int_literal", "float_literal",
    "imaginary_literal", "decimal_integer_literal", "hex_integer_literal", "decimal_floating_point_literal",
    "string_literal", "character_literal", "null_literal", "escape_sequence", "string_start", "string_end",
}
COMMENT_TYPES = {"comment", "line_comment", "block_comment"}

NESTED_FUNCTION_TYPES = {
    "function_definition", "function_declaration", "method_definition", "generator_function_declaration",
    "method_declaration", "constructor_declaration", "class_definition", "class_declaration",
}

DECISION_TYPES = {
    "python": {"if_statement", "elif_clause", "for_statement", "while_statement", "except_clause",
               "conditional_expression", "for_in_clause", "if_clause", "case_clause"},
    "js": {"if_statement", "for_statement", "for_in_statement", "while_statement", "do_statement",
           "switch_case", "catch_clause", "ternary_expression"},
    "go": {"if_statement", "for_statement", "expression_case", "type_case", "communication_case"},
    "java": {"if_statement", "for_statement", "enhanced_for_statement", "while_statement", "do_statement",
             "switch_label", "catch_clause", "ternary_expression"},
}
LOGICAL_OPERATORS = {"&&", "||", "??", "and", "or"}


class _Token:
    """Stand-in node that just emits fixed text into the token stream."""
    is_named = False
    child_count = 0

    def __init__(self, text: str):
        self.type = text
        self.text = text.encode()

    def child_by_field_name(self, _):
        return None


_Close = _Token(")")
_NestedDef = _Token("NESTED_DEF")  # keeps "has an inner def here" in the fingerprint


def _children_skipping_nested(node):
    for c in node.children:
        yield _NestedDef if c.type in NESTED_FUNCTION_TYPES else c


def function_metrics(func_node, fam: str) -> dict:
    """{"hash": str, "size": int, "complexity": int} for one function node."""
    tokens = []
    size = 0
    complexity = 1
    decisions = DECISION_TYPES[fam]
    stack = list(reversed(list(_children_skipping_nested(func_node))))
    while stack:
        node = stack.pop()
        t = node.type
        if t in COMMENT_TYPES:
            continue
        if node.is_named:
            size += 1
        if t in decisions:
            complexity += 1
        elif t in ("binary_expression", "boolean_operator"):
            op = node.child_by_field_name("operator")
            if op is not None and op.type in LOGICAL_OPERATORS:
                complexity += 1

        if t in IDENTIFIER_TYPES:
            tokens.append("ID")
        elif t in LITERAL_TYPES:
            tokens.append("LIT")
            continue  # don't descend into string contents
        elif node.child_count == 0:
            tokens.append(node.text.decode(errors="replace") if not node.is_named else t)
        else:
            tokens.append("(" + t)
            stack.append(_Close)
            stack.extend(reversed(list(_children_skipping_nested(node))))
    digest = hashlib.blake2b(" ".join(tokens).encode(), digest_size=12).hexdigest()
    return {"hash": digest, "size": size, "complexity": complexity}

