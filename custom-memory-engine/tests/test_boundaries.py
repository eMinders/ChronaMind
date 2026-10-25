"""Enforce standalone-package imports and decision-link traceability."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = ROOT.parent
FORBIDDEN_MODULES = frozenset({"app", "evals", "scripts", "mem0", "mem0ai", "langgraph", "fastapi"})


def find_forbidden_imports(source_code: str, forbidden: frozenset[str] = FORBIDDEN_MODULES) -> list[str]:
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
            top_level = name.split(".")[0]
            if top_level in forbidden:
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
            violations = find_forbidden_imports(path.read_text(encoding="utf-8"))
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
