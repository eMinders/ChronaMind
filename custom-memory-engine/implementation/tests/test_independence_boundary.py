"""AST import-boundary guard for custom-memory-engine Python sources."""

import ast
from pathlib import Path

CME_ROOT = Path(__file__).resolve().parents[2]
FORBIDDEN_TOP_LEVEL = frozenset({"app", "evals", "scripts"})


def find_forbidden_imports(source_code: str, forbidden: frozenset[str] = FORBIDDEN_TOP_LEVEL) -> list[str]:
    """Return any top-level module names in `source_code` that match `forbidden`."""
    tree = ast.parse(source_code)
    violations: list[str] = []
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            if name.split(".")[0] in forbidden:
                violations.append(name)
    return violations


def test_cme_has_no_root_app_imports() -> None:
    scan_dirs = [
        CME_ROOT / "implementation",
        CME_ROOT / "evaluation",
        CME_ROOT / "application",
        CME_ROOT / "research",
        CME_ROOT / "src",
        CME_ROOT / "tests",
    ]
    for directory in scan_dirs:
        if not directory.exists():
            continue
        for py_file in sorted(directory.rglob("*.py")):
            violations = find_forbidden_imports(py_file.read_text(encoding="utf-8"))
            assert not violations, f"Forbidden import(s) {violations} found in {py_file}"


def test_boundary_guard_catches_synthetic_app_import() -> None:
    assert find_forbidden_imports("import app\n") == ["app"]
    assert find_forbidden_imports("from app.services import memory\n") == ["app.services"]
    assert find_forbidden_imports("from evals.metrics import runner\n") == ["evals.metrics"]
    assert find_forbidden_imports("import scripts.setup\n") == ["scripts.setup"]
    assert find_forbidden_imports("import hashlib\n") == []
