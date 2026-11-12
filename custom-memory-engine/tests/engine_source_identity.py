"""The engine's source does not vary by profile: a bounded static fork audit.

Limitations: aliases, computed names, getattr/importlib, dynamic dispatch and
semantic equivalence are not resolved. A structural exclusive-field finding is
syntactic, not proof a profile passes the strict schema. Schema-declared fields,
including defaults, are allowed, as are ordinary configuration values. Direct
subscripting of a profile-exclusive map key is flagged even if guarded; .get()
reads are allowed, so branching via .get() can evade this check. Schema traversal
only resolves directly named model annotations and declared mapping annotations.
Profiles/*.json are input data; tests, caches and bytecode are excluded. Other
engine Python and source assets (including SQL) are included. The existing
profile.py __all__ export for the public conversational loader is narrowly
exempted; it is an exported function name, not a profile-dependent dispatch.
This does not establish runtime parity or the G2 generality gate.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from tests.profile_branching import find_profile_branching, is_profile_identity

ROOT = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine"


@dataclass(frozen=True)
class Audit:
    """Discovered profile names and deterministically ordered findings."""

    profile_names: tuple[str, ...]
    findings: tuple[str, ...]


def _documents(root: Path) -> list[dict[str, object]]:
    """Read all profile JSON inputs, failing closed on missing or malformed inputs."""
    paths = sorted((root / "profiles").glob("*.json"))
    if len(paths) < 2:
        raise ValueError(f"{root / 'profiles'}: at least two profile documents are required")
    documents = []
    names: set[str] = set()
    for path in paths:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            raise ValueError(f"{path}: invalid profile document: {exc}") from exc
        if not isinstance(doc, dict) or not isinstance(doc.get("name"), str) or not doc["name"]:
            raise ValueError(f"{path}: profile name must be a non-empty string")
        if doc["name"] in names:
            raise ValueError(f"{path}: duplicate profile name {doc['name']}")
        names.add(doc["name"])
        documents.append(doc)
    return documents


def _fields(value: object, prefix: tuple[str, ...] = ()) -> set[tuple[str, ...]]:
    """Collect raw dictionary paths; callers exclude declared dynamic map keys."""
    found: set[tuple[str, ...]] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if isinstance(key, str):
                path = (*prefix, key)
                found.add(path)
                found.update(_fields(child, path))
    return found


def _schema_fields(root: Path) -> tuple[set[tuple[str, ...]], set[tuple[str, ...]]]:
    """Find Profile model field paths and its declared dynamic-map paths."""
    path = root / "profile.py"
    if not path.exists():
        return set(), set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    models = {node.name: node for node in tree.body if isinstance(node, ast.ClassDef)}
    fields: set[tuple[str, ...]] = set()
    maps: set[tuple[str, ...]] = set()

    def visit(name: str, prefix: tuple[str, ...], ancestors: frozenset[str]) -> None:
        if name not in models or name in ancestors:
            return
        for item in models[name].body:
            if not isinstance(item, ast.AnnAssign) or not isinstance(item.target, ast.Name):
                continue
            field = (*prefix, item.target.id)
            fields.add(field)
            annotation = item.annotation
            if isinstance(annotation, ast.Name):
                visit(annotation.id, field, ancestors | {name})
            elif isinstance(annotation, ast.Subscript) and isinstance(annotation.value, ast.Name):
                if annotation.value.id in {"dict", "Dict", "Mapping", "MutableMapping"}:
                    maps.add(field)

    visit("Profile", (), frozenset())
    return fields, maps


def _exclusive_fields(
    documents: list[dict[str, object]], schema_fields: set[tuple[str, ...]], map_paths: set[tuple[str, ...]]
) -> set[tuple[str, ...]]:
    """Find exclusive raw paths, excluding qualified schema fields and dynamic-map keys."""
    paths = [_fields(doc) for doc in documents]
    return {
        path
        for path in set.union(*paths)
        if 0 < sum(path in item for item in paths) < len(paths)
        and path not in schema_fields
        and not any(path[: len(mapping)] == mapping for mapping in map_paths)
    }


def _exclusive_map_keys(documents: list[dict[str, object]], map_paths: set[tuple[str, ...]]) -> set[tuple[str, ...]]:
    """Find keys absent from at least one profile's declared dynamic map."""
    paths = [_fields(doc) for doc in documents]
    return {
        path
        for path in set.union(*paths)
        if path[:-1] in map_paths and 0 < sum(path in item for item in paths) < len(paths)
    }


def _attribute_path(node: ast.AST) -> tuple[str, ...] | None:
    """Resolve direct profile attributes including self.profile; dynamic accesses are out of scope."""
    parts = []
    while isinstance(node, ast.Attribute):
        if node.attr == "profile" or node.attr.endswith("_profile"):
            return tuple(reversed(parts))
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name) and (node.id == "profile" or node.id.endswith("_profile")):
        return tuple(reversed(parts))
    return None


def _export_exemptions(tree: ast.Module, path: Path, root: Path) -> set[tuple[int, int]]:
    """Exempt only profile.py's existing conversational public export entry."""
    if path != root / "profile.py":
        return set()
    allowed: set[tuple[int, int]] = set()
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not any(
            isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets
        ):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple)):
            allowed.update(
                (entry.lineno, entry.col_offset)
                for entry in node.value.elts
                if isinstance(entry, ast.Constant) and entry.value == "conversational"
            )
    return allowed


def _identity_findings(source: str, tree: ast.Module, display: str) -> set[str]:
    """Reuse the identity scanner and catch direct identity conditions."""
    findings = {f"{display}:{line}: identity: {detail}" for line, detail in find_profile_branching(source)}
    for node in ast.walk(tree):
        condition = node.test if isinstance(node, (ast.If, ast.IfExp, ast.While)) else None
        if isinstance(node, ast.match_case):
            condition = node.guard
        if condition is not None and any(is_profile_identity(child) for child in ast.walk(condition)):
            findings.add(f"{display}:{condition.lineno}: identity: branches on a profile identity")
    return findings


def _literal_findings(tree: ast.Module, path: Path, root: Path, names: set[str]) -> set[str]:
    """Find literal profile names apart from the existing public export."""
    display = path.relative_to(root).as_posix()
    exempt = _export_exemptions(tree, path, root)
    findings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in names:
            if (node.lineno, node.col_offset) not in exempt:
                findings.add(f"{display}:{node.lineno}: profile-name: literal {node.value!r}")
    return findings


def _import_findings(tree: ast.Module, path: Path, root: Path, names: set[str]) -> set[str]:
    """Find static engine imports of profile-specific modules."""
    display = path.relative_to(root).as_posix()
    findings: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            if isinstance(node, ast.ImportFrom):
                modules.extend((node.module or "") + "." + alias.name for alias in node.names)
                if node.level:
                    package = path.relative_to(root).parts[:-1]
                    if node.level - 1 <= len(package):
                        base = package[: len(package) - node.level + 1]
                        modules.extend(".".join((*base, module.lstrip("."))) for module in list(modules))
            if any(
                any(part in names for part in module.split(".") if part)
                and (isinstance(node, ast.ImportFrom) and node.level > 0 or module.startswith("custom_memory_engine."))
                for module in modules
            ):
                findings.add(f"{display}:{node.lineno}: profile-specific: import from profile package")
    return findings


def _exclusive_field_findings(tree: ast.Module, display: str, exclusive: set[tuple[str, ...]]) -> set[str]:
    """Find direct attribute reads of structurally exclusive fields."""
    findings: set[str] = set()
    for node in ast.walk(tree):
        field = _attribute_path(node)
        if isinstance(node, ast.Attribute) and field in exclusive:
            findings.add(f"{display}:{node.lineno}: exclusive-field: profile.{'.'.join(field)}")
    return findings


def _exclusive_key_findings(tree: ast.Module, display: str, exclusive_keys: set[tuple[str, ...]]) -> set[str]:
    """Find direct index accesses to profile-specific map keys."""
    findings: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Subscript)
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            mapping = _attribute_path(node.value)
            if mapping is not None and (*mapping, node.slice.value) in exclusive_keys:
                findings.add(
                    f"{display}:{node.lineno}: exclusive-key: profile.{'.'.join(mapping)}[{node.slice.value!r}]"
                )
    return findings


def _python_findings(
    path: Path,
    root: Path,
    names: set[str],
    exclusive: set[tuple[str, ...]],
    exclusive_keys: set[tuple[str, ...]],
) -> set[str]:
    """Parse Python source and combine findings from each rule."""
    source = path.read_text(encoding="utf-8-sig")
    tree = ast.parse(source, filename=str(path))
    display = path.relative_to(root).as_posix()
    return (
        _identity_findings(source, tree, display)
        | _literal_findings(tree, path, root, names)
        | _import_findings(tree, path, root, names)
        | _exclusive_field_findings(tree, display, exclusive)
        | _exclusive_key_findings(tree, display, exclusive_keys)
    )


def audit_engine(root: Path = ROOT) -> Audit:
    """Audit one engine tree against every discovered profile document."""
    root = root.resolve()
    documents = _documents(root)
    names = {str(doc["name"]) for doc in documents}
    schema_fields, map_paths = _schema_fields(root)
    exclusive = _exclusive_fields(documents, schema_fields, map_paths)
    exclusive_keys = _exclusive_map_keys(documents, map_paths)
    findings: set[str] = set()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        relative = path.relative_to(root)
        if relative.parent == Path("profiles") and path.suffix == ".json":
            continue
        if any(part in names for part in relative.parts[:-1]) or path.stem in names:
            findings.add(f"{relative.as_posix()}:1: profile-specific: profile-specific source path")
        if path.suffix == ".py":
            findings.update(_python_findings(path, root, names, exclusive, exclusive_keys))
        else:
            raw = path.read_bytes()
            for name in names:
                quoted = re.compile(rb"(['\"])" + re.escape(name.encode("utf-8")) + rb"\1")
                for match in quoted.finditer(raw):
                    line = raw.count(b"\n", 0, match.start()) + 1
                    findings.add(f"{relative.as_posix()}:{line}: profile-name: quoted literal {name!r}")
    return Audit(tuple(sorted(names)), tuple(sorted(findings)))


def main(argv: Sequence[str] | None = None) -> int:
    """Exit nonzero on findings or unreadable/invalid audit inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="engine source tree containing profiles/*.json")
    args = parser.parse_args(argv)
    try:
        result = audit_engine(args.root)
    except (OSError, ValueError, SyntaxError, UnicodeError) as exc:
        print(f"audit input error: {exc}", file=sys.stderr)
        return 2
    for finding in result.findings:
        print(finding)
    if result.findings:
        return 1
    print(f"engine source identity: PASS ({len(result.profile_names)} profiles: {', '.join(result.profile_names)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
