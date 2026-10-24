"""Enforce standalone-package imports and decision-link traceability."""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_no_product_or_baseline_imports() -> None:
    forbidden = {"app", "evals", "scripts", "mem0", "mem0ai", "langgraph", "fastapi"}
    for path in (ROOT / "src").rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            assert not any(name.split(".")[0] in forbidden for name in names), path


def test_six_memos_and_reference_links() -> None:
    memos = sorted((ROOT / "decisions").glob("RL-*.md"))
    assert len(memos) == 6
    register = (ROOT.parent / "research-lab/PLAN/DECISIONS_REGISTER.md").read_text()
    for memo in memos:
        assert memo.name in register
        assert "## Sign-off" in memo.read_text() or "sign-off" in memo.read_text().lower()
    for path in [
        ROOT / "README.md",
        ROOT / "evidence/SOURCES.md",
        ROOT / "evidence/VERIFICATION.md",
        *memos,
        ROOT.parent / "research-lab/PLAN/DECISIONS_REGISTER.md",
    ]:
        for target in re.findall(r"\]\(([^)]+)\)", path.read_text()):
            if "://" in target or target.startswith("#"):
                continue
            assert (path.parent / target.split("#")[0]).exists(), (path, target)
