"""Artifact paths reject pre-existing links/reparse points and accidental reuse."""

from pathlib import Path
from stat import FILE_ATTRIBUTE_REPARSE_POINT

from evaluation.harness.config import safe_component


def reject_links(path: Path) -> None:
    """Check each existing ancestor before following or writing any destination."""
    if ".." in path.parts:
        raise ValueError("parent_traversal_not_allowed")
    absolute = path.absolute()
    for part in reversed((absolute, *absolute.parents)):
        if part.is_symlink() or (
            part.exists() and getattr(part.lstat(), "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError("linked_artifact_path_not_allowed")


def directory(path: Path) -> Path:
    """Create ordinary directories only; never replace anything already present."""
    reject_links(path)
    path.mkdir(parents=True, exist_ok=True)
    reject_links(path)
    if not path.is_dir():
        raise ValueError("artifact_directory_required")
    return path


def run_destination(root: Path, run_id: str, config_id: str, suite_id: str) -> Path:
    """Validate components without creating an output directory or reading inputs."""
    for component in (run_id, config_id, suite_id):
        safe_component(component)
    result = root / run_id / config_id / suite_id
    reject_links(result)
    if result.exists():
        raise FileExistsError("immutable_run_already_exists")
    return result
