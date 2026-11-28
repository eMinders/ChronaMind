"""Detect code that branches on a profile's identity (V2-D08, E-019; reusable by E-025's fork audit).

A profile is configuration. Code that compares a profile's *name* to a literal, matches on it, or looks up a
handler by it is a fork in disguise. This scanner flags those shapes in a Python source string:

* ``if profile.name == "coding"`` / ``!=`` / ``in ("a", "b")`` / ``is``;
* ``match profile.name:``;
* ``handlers[profile.name]`` (dispatch by identity).

It deliberately ignores comparisons on any other attribute of a profile (``profile.kinds``,
``profile.scope_root``), because configuration may be read freely; only identity is forbidden.
"""

import ast
from pathlib import Path

IDENTITY_NAMES = frozenset({"profile_name", "profile_id", "profile_key"})
IDENTITY_ATTRS = frozenset({"name", "id", "key"})


def _is_profile_object(node: ast.AST) -> bool:
    if isinstance(node, ast.Name):
        return node.id == "profile" or node.id.endswith("_profile")
    if isinstance(node, ast.Attribute):
        return node.attr == "profile" or node.attr.endswith("_profile")
    return False


def is_profile_identity(node: ast.AST) -> bool:
    """Whether ``node`` reads a profile's name, id or key."""
    if isinstance(node, ast.Name):
        return node.id in IDENTITY_NAMES
    if isinstance(node, ast.Attribute):
        if node.attr in IDENTITY_NAMES:
            return True
        return node.attr in IDENTITY_ATTRS and _is_profile_object(node.value)
    return False


def _contains_identity(node: ast.AST) -> bool:
    return any(is_profile_identity(child) for child in ast.walk(node))


def _is_literal_text(node: ast.AST) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str)
    if isinstance(node, ast.Tuple | ast.List | ast.Set):
        return bool(node.elts) and all(_is_literal_text(e) for e in node.elts)
    return False


def find_profile_branching(source: str) -> list[tuple[int, str]]:
    """Return ``(line, reason)`` for every place ``source`` branches on a profile's identity."""
    findings: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Compare):
            operands = [node.left, *node.comparators]
            if any(_contains_identity(o) for o in operands) and any(_is_literal_text(o) for o in operands):
                findings.append((node.lineno, "compares a profile identity to a literal"))
        elif isinstance(node, ast.Match):
            if _contains_identity(node.subject):
                findings.append((node.lineno, "matches on a profile identity"))
        elif isinstance(node, ast.Subscript):
            if _contains_identity(node.slice):
                findings.append((node.lineno, "dispatches on a profile identity"))
    return sorted(findings)


def scan_tree(root: Path) -> dict[str, list[tuple[int, str]]]:
    """Scan every ``*.py`` file under ``root`` and return the files that branch on a profile identity."""
    found: dict[str, list[tuple[int, str]]] = {}
    for path in sorted(root.rglob("*.py")):
        findings = find_profile_branching(path.read_text(encoding="utf-8"))
        if findings:
            found[str(path)] = findings
    return found
