"""Controls for the profile-independent engine-source audit."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from custom_memory_engine.profile import load_profile
from tests.engine_source_identity import audit_engine, main


def make_tree(tmp_path: Path) -> tuple[Path, Path]:
    """Build two synthetic profile documents and one neutral engine module."""
    root = tmp_path / "custom_memory_engine"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "alpha.json").write_text(
        json.dumps({"name": "alpha", "kinds": ["claim"], "shared": True}), encoding="utf-8"
    )
    (profiles / "beta.json").write_text(
        json.dumps({"name": "beta", "kinds": ["episode"], "shared": False, "beta_only": True}), encoding="utf-8"
    )
    (root / "module.py").write_text('def run(profile):\n    return "claim" in profile.kinds\n', encoding="utf-8")
    return root, profiles


def test_real_engine_passes_and_profile_documents_are_discovered() -> None:
    root = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine"
    result = audit_engine(root)
    assert {"coding", "conversational"} <= set(result.profile_names)
    assert not result.findings, result.findings


@pytest.mark.parametrize(
    ("code", "rule"),
    [
        ('if profile.name == "beta":\n    pass\n', "identity"),
        ("from custom_memory_engine.profiles.beta import run\n", "profile-specific"),
        ("if profile.beta_only:\n    pass\n", "exclusive-field"),
    ],
    ids=["identity", "profile-specific-import", "exclusive-field-branch"],
)
def test_violation_names_file_line_and_rule(tmp_path: Path, code: str, rule: str) -> None:
    root, _ = make_tree(tmp_path)
    (root / "violation.py").write_text("# mutation\n" + code, encoding="utf-8")
    result = audit_engine(root)
    assert any("violation.py:2:" in finding and rule in finding for finding in result.findings), result.findings


def test_common_configuration_reads_are_permitted(tmp_path: Path) -> None:
    root, _ = make_tree(tmp_path)
    (root / "module.py").write_text(
        'if profile.shared:\n    result = "claim" in profile.kinds\nelse:\n    result = profile.kinds\n',
        encoding="utf-8",
    )
    assert not audit_engine(root).findings


def test_new_profile_is_discovered_without_changing_the_scanner(tmp_path: Path) -> None:
    root, profiles = make_tree(tmp_path)
    (profiles / "gamma.json").write_text(json.dumps({"name": "gamma", "gamma_only": True}), encoding="utf-8")
    (root / "violation.py").write_text("if profile.gamma_only:\n    pass\n", encoding="utf-8")
    result = audit_engine(root)
    assert set(result.profile_names) == {"alpha", "beta", "gamma"}
    assert any("violation.py:1:" in finding and "exclusive-field" in finding for finding in result.findings)


def test_profile_specific_python_file_is_detected(tmp_path: Path) -> None:
    root, profiles = make_tree(tmp_path)
    (profiles / "beta").mkdir()
    (profiles / "beta" / "handler.py").write_text("pass\n", encoding="utf-8")
    assert any("profile-specific" in finding and "handler.py:1:" in finding for finding in audit_engine(root).findings)


@pytest.mark.parametrize(
    "code",
    ["if self.profile.beta_only:\n    pass\n", "match profile.beta_only:\n    case True:\n        pass\n"],
    ids=["instance-profile", "match-subject"],
)
def test_direct_exclusive_field_controls_are_detected(tmp_path: Path, code: str) -> None:
    root, _ = make_tree(tmp_path)
    (root / "control.py").write_text(code, encoding="utf-8")
    assert any("control.py:1: exclusive-field:" in item for item in audit_engine(root).findings)


def test_identity_method_branch_is_detected(tmp_path: Path) -> None:
    root, _ = make_tree(tmp_path)
    (root / "control.py").write_text('if profile.name.startswith("bet"):\n    pass\n', encoding="utf-8")
    assert any("control.py:1: identity:" in item for item in audit_engine(root).findings)


def test_exclusive_field_return_is_detected(tmp_path: Path) -> None:
    root, _ = make_tree(tmp_path)
    (root / "control.py").write_text("def run(profile):\n    return profile.beta_only\n", encoding="utf-8")
    assert any("control.py:2: exclusive-field:" in item for item in audit_engine(root).findings)


def test_field_present_in_two_of_three_profiles_is_detected(tmp_path: Path) -> None:
    root, profiles = make_tree(tmp_path)
    (profiles / "gamma.json").write_text(json.dumps({"name": "gamma", "beta_only": True}), encoding="utf-8")
    (root / "control.py").write_text("value = profile.beta_only\n", encoding="utf-8")
    assert any("control.py:1: exclusive-field:" in item for item in audit_engine(root).findings)


def test_profile_specific_module_and_import_outside_profiles(tmp_path: Path) -> None:
    root, _ = make_tree(tmp_path)
    (root / "beta.py").write_text("def run(): pass\n", encoding="utf-8")
    (root / "control.py").write_text("from custom_memory_engine import beta\n", encoding="utf-8")
    findings = audit_engine(root).findings
    assert any("beta.py:1: profile-specific:" in item for item in findings)
    assert any("control.py:1: profile-specific:" in item for item in findings)


def test_profile_named_sql_asset_is_detected(tmp_path: Path) -> None:
    root, _ = make_tree(tmp_path)
    (root / "beta.sql").write_text("SELECT 2;\n", encoding="utf-8")
    assert any("beta.sql:1: profile-specific:" in item for item in audit_engine(root).findings)


def test_profile_specific_directory_contains_sql(tmp_path: Path) -> None:
    root, profiles = make_tree(tmp_path)
    (profiles / "beta").mkdir()
    (profiles / "beta" / "query.sql").write_text("SELECT 2;\n", encoding="utf-8")
    assert any("profiles/beta/query.sql:1: profile-specific:" in item for item in audit_engine(root).findings)


def test_unrelated_type_annotation_cannot_hide_exclusive_field(tmp_path: Path) -> None:
    root, _ = make_tree(tmp_path)
    (root / "profile.py").write_text("class Unrelated:\n    beta_only: bool\n", encoding="utf-8")
    (root / "control.py").write_text("if profile.beta_only:\n    pass\n", encoding="utf-8")
    assert any("control.py:1: exclusive-field:" in item for item in audit_engine(root).findings)


def test_relative_import_from_profile_package_is_detected(tmp_path: Path) -> None:
    root, profiles = make_tree(tmp_path)
    (profiles / "dispatch.py").write_text("from . import beta\n", encoding="utf-8")
    assert any("profiles/dispatch.py:1: profile-specific:" in item for item in audit_engine(root).findings)


def copy_real_profiles(tmp_path: Path) -> tuple[Path, Path]:
    """Use actual schema and profile documents so field controls cover valid inputs."""
    source = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine"
    root = tmp_path / "custom_memory_engine"
    profiles = root / "profiles"
    profiles.mkdir(parents=True)
    shutil.copyfile(source / "profile.py", root / "profile.py")
    for path in sorted((source / "profiles").glob("*.json")):
        shutil.copyfile(path, profiles / path.name)
    return root, profiles


def test_exclusive_direct_config_key_branch_with_valid_profiles(tmp_path: Path) -> None:
    root, profiles = copy_real_profiles(tmp_path)
    assert all(load_profile(path) for path in sorted(profiles.glob("*.json")))
    (root / "control.py").write_text('if profile.retention.defaults["procedure"]:\n    pass\n', encoding="utf-8")
    assert any("control.py:1: exclusive-key:" in item for item in audit_engine(root).findings)


def test_exclusive_key_return_with_valid_profiles(tmp_path: Path) -> None:
    root, profiles = copy_real_profiles(tmp_path)
    assert all(load_profile(path) for path in sorted(profiles.glob("*.json")))
    (root / "control.py").write_text(
        'def run(profile):\n    return profile.retention.defaults["procedure"]\n', encoding="utf-8"
    )
    assert any("control.py:2: exclusive-key:" in item for item in audit_engine(root).findings)


def test_key_present_in_two_of_three_profiles_is_detected(tmp_path: Path) -> None:
    root, profiles = copy_real_profiles(tmp_path)
    coding = json.loads((profiles / "coding.json").read_text(encoding="utf-8"))
    coding["name"] = "third"
    (profiles / "third.json").write_text(json.dumps(coding), encoding="utf-8")
    (root / "control.py").write_text('value = profile.retention.defaults["procedure"]\n', encoding="utf-8")
    assert any("control.py:1: exclusive-key:" in item for item in audit_engine(root).findings)


def test_defaulted_schema_field_and_safe_mapping_reads_are_allowed(tmp_path: Path) -> None:
    root, profiles = copy_real_profiles(tmp_path)
    path = profiles / "conversational.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    del document["retention"]["promote_to_durable"]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert all(load_profile(path) for path in sorted(profiles.glob("*.json")))
    (root / "control.py").write_text(
        "if self.profile.retention.promote_to_durable:\n    pass\n"
        'if profile.retention.defaults.get("procedure"):\n    pass\n',
        encoding="utf-8",
    )
    assert not audit_engine(root).findings


def test_sql_profile_literal_is_detected(tmp_path: Path) -> None:
    root, _ = make_tree(tmp_path)
    (root / "rules.sql").write_text("SELECT 1;\nSELECT 'beta';\n", encoding="utf-8")
    assert any("rules.sql:2:" in finding and "profile-name" in finding for finding in audit_engine(root).findings)


def test_existing_conversational_public_export_is_narrowly_exempt(tmp_path: Path) -> None:
    root, profiles = make_tree(tmp_path)
    (profiles / "conversational.json").write_text(
        json.dumps({"name": "conversational", "shared": True, "kinds": ["claim"]}), encoding="utf-8"
    )
    (root / "profile.py").write_text('__all__ = ["conversational"]\n', encoding="utf-8")
    assert not audit_engine(root).findings
    (root / "module.py").write_text('x = "conversational"\n', encoding="utf-8")
    assert any("module.py:1:" in finding for finding in audit_engine(root).findings)


def test_invalid_profile_input_and_syntax_fail_closed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root, profiles = make_tree(tmp_path)
    (profiles / "broken.json").write_text("{", encoding="utf-8")
    assert main(["--root", str(root)]) != 0
    assert "broken.json" in capsys.readouterr().err
    (profiles / "broken.json").unlink()
    (root / "bad.py").write_text("if :\n", encoding="utf-8")
    assert main(["--root", str(root)]) != 0
    assert "bad.py" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("code", "rule"),
    [
        ('if profile.name == "beta":\n    pass\n', "identity"),
        ("from custom_memory_engine.profiles.beta import run\n", "profile-specific"),
        ("if profile.beta_only:\n    pass\n", "exclusive-field"),
    ],
    ids=["identity", "profile-specific-import", "exclusive-field-branch"],
)
def test_cli_reports_nonzero_on_actual_violation(tmp_path: Path, code: str, rule: str) -> None:
    root, _ = make_tree(tmp_path)
    (root / "bad.py").write_text(code, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "tests.engine_source_identity", "--root", str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert f"bad.py:1: {rule}:" in result.stdout
