"""Enforce standalone-package imports and decision-link traceability."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
FORBIDDEN_MODULES = frozenset({"app", "evals", "scripts", "mem0", "mem0ai", "graphiti_core", "langgraph", "fastapi"})
VENDOR_EXCEPTIONS = {
    "evaluation/baselines/b3_mem0.py": frozenset({"mem0", "mem0ai"}),
    "evaluation/baselines/b4_graphiti.py": frozenset({"graphiti_core"}),
    "evaluation/baselines/tests/test_graphiti_integration.py": frozenset({"graphiti_core"}),
}


def find_forbidden_imports(
    source_code: str, forbidden: frozenset[str] = FORBIDDEN_MODULES, *, relative_path: str | None = None
) -> list[str]:
    """Return any top-level module names in `source_code` that match `forbidden`."""
    tree = ast.parse(source_code)
    violations: list[str] = []
    allowed = VENDOR_EXCEPTIONS.get(relative_path or "", frozenset())
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        elif isinstance(node, ast.Call) and node.args:
            function = node.func
            if (
                (
                    isinstance(function, ast.Name)
                    and function.id == "__import__"
                    or isinstance(function, ast.Attribute)
                    and function.attr == "import_module"
                )
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                names = [node.args[0].value]
        for name in names:
            top_level = name.split(".")[0]
            if top_level in forbidden and top_level not in allowed:
                violations.append(name)
    return violations


def test_no_product_or_baseline_imports() -> None:
    scan_roots = [
        ROOT / "src",
        ROOT / "tests",
        ROOT / "implementation",
        ROOT / "evaluation",
        ROOT / "application",
        ROOT / "research",
    ]
    scanned_count = 0
    for scan_root in scan_roots:
        if not scan_root.exists():
            continue
        for path in sorted(scan_root.rglob("*.py")):
            scanned_count += 1
            violations = find_forbidden_imports(
                path.read_text(encoding="utf-8"), relative_path=path.relative_to(ROOT).as_posix()
            )
            assert not violations, f"Forbidden import(s) {violations} in {path}"
    assert scanned_count >= 5


def test_synthetic_forbidden_imports_are_rejected() -> None:
    for snippet in (
        "import app\n",
        "from app.core.config import settings\n",
        "import evals.runner\n",
        "from scripts import setup\n",
        "import mem0ai\n",
    ):
        assert find_forbidden_imports(snippet), f"Expected violation for snippet: {snippet!r}"
    assert find_forbidden_imports("import json\nfrom pathlib import Path\n") == []


def test_dynamic_vendor_imports_cannot_bypass_boundary() -> None:
    for snippet in (
        'import importlib\nimportlib.import_module("mem0.memory.main")\n',
        '__import__("app.core.config")\n',
    ):
        assert find_forbidden_imports(snippet)


def test_vendor_exception_matches_only_the_designated_worker() -> None:
    for module, worker in (("mem0", "b3_mem0.py"), ("graphiti_core", "b4_graphiti.py")):
        source = f"import {module}\nfrom app.core import config\n"
        assert find_forbidden_imports(source, relative_path=f"evaluation/baselines/{worker}") == ["app.core"]
        for wrong_path in (f"implementation/{worker}", f"evaluation/baselines/tests/{worker}", "evaluation/x.py"):
            assert find_forbidden_imports(source, relative_path=wrong_path) == [module, "app.core"]


def test_six_memos_and_reference_links() -> None:
    memos = sorted((ROOT / "decisions").glob("RL-*.md"))
    assert len(memos) == 6
    plan_memos = sorted((ROOT / "PLAN/decisions").glob("RL-*.md"))
    assert len(plan_memos) == 6
    register = (ROOT / "PLAN/DECISIONS_REGISTER.md").read_text(encoding="utf-8")
    for memo in memos:
        assert memo.name in register
        assert (
            "## Sign-off" in memo.read_text(encoding="utf-8") or "sign-off" in memo.read_text(encoding="utf-8").lower()
        )
    for path in [
        ROOT / "README.md",
        ROOT / "evidence/SOURCES.md",
        ROOT / "evidence/VERIFICATION.md",
        *memos,
        *plan_memos,
        ROOT / "PLAN/DECISIONS_REGISTER.md",
    ]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8")):
            if "://" in target or target.startswith("#"):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
