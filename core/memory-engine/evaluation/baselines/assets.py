"""Pinned local model assets; downloads occur only through the explicit prepare CLI."""

import argparse
import hashlib
import json
import os
import tempfile
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from rich.console import Console

from evaluation.baselines.contracts import EMBEDDING_MODEL, EMBEDDING_REVISION

O200K_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"
O200K_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"


@dataclass(frozen=True)
class AssetPin:
    sha256: str
    size: int
    git_blob: str
    lfs: bool = False


# Official immutable HF tree metadata: regular Git blobs checked by Git SHA1 and
# SHA256; the ONNX artifact's SHA256 is its LFS object ID, not its pointer blob ID.
BGE_FILES = {
    "1_Pooling/config.json": AssetPin(
        "d1caf60c96f5fba2157c0c26b76d80818fad6cf0b8eb5e73ec372ff9818eba5c",
        190,
        "1da070f0993788c8851a652170ff013aa54dd519",
    ),
    "config.json": AssetPin(
        "094f8e891b932f2000c92cfc663bac4c62069f5d8af5b5278c4306aef3084750",
        743,
        "3992bf890728a92476c700e6e657fd696eb94160",
    ),
    "onnx/model.onnx": AssetPin(
        "828e1496d7fabb79cfa4dcd84fa38625c0d3d21da474a00f08db0f559940cf35",
        133_093_490,
        "33c23a71d9651ddba17f6a52eecf93658fc90973",
        True,
    ),
    "special_tokens_map.json": AssetPin(
        "b6d346be366a7d1d48332dbc9fdf3bf8960b5d879522b7799ddba59e76237ee3",
        125,
        "a8b3208c2884c4efb86e49300fdd3dc877220cdf",
    ),
    "tokenizer.json": AssetPin(
        "d241a60d5e8f04cc1b2b3e9ef7a4921b27bf526d9f6050ab90f9267a1f9e5c66",
        711_396,
        "688882a79f44442ddc1f60d70334a7ff5df0fb47",
    ),
    "tokenizer_config.json": AssetPin(
        "9261e7d79b44c8195c1cada2b453e55b00aeb81e907a6664974b4d7776172ab3",
        366,
        "37fca74771bc76a8e01178ce3a6055a0995f8093",
    ),
}


def _unlinked(path: Path) -> None:
    for component in (path, *path.absolute().parents):
        if component.is_symlink() or component.is_junction():
            raise ValueError("asset_link_rejected")


def local_path(directory: Path, relative: str) -> Path:
    """Reject linked assets and any escape from the explicitly supplied directory."""
    base = directory.absolute()
    _unlinked(base)
    target = base / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError("asset_path_rejected")
    current = target
    while current != base:
        _unlinked(current)
        current = current.parent
    if not target.resolve().is_relative_to(base.resolve()):
        raise ValueError("asset_path_rejected")
    return target


def verified_bytes(path: Path, sha256: str, size: int | None = None) -> bytes:
    _unlinked(path)
    try:
        if size is not None and path.stat().st_size != size:
            raise ValueError("asset_size_mismatch")
        content = path.read_bytes()
    except OSError:
        raise ValueError("asset_unavailable") from None
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError("asset_hash_mismatch")
    return content


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "model_id": EMBEDDING_MODEL,
        "revision": EMBEDDING_REVISION,
        "files": {name: asdict(pin) for name, pin in BGE_FILES.items()},
    }


def write_manifest(directory: Path) -> Path:
    """Write only the known immutable pins; never adopt hashes from arbitrary files."""
    target = local_path(directory, "manifest.json")
    encoded = (json.dumps(_manifest(), sort_keys=True, indent=2) + "\n").encode()
    if target.exists() and target.read_bytes() != encoded:
        raise ValueError("asset_manifest_conflict")
    target.write_bytes(encoded)
    return target


def verify_assets(directory: Path) -> dict[str, bytes]:
    """Local verification; caller cannot authorize another model by editing a manifest."""
    manifest_path = local_path(directory, "manifest.json")
    try:
        manifest = json.loads(manifest_path.read_bytes())
    except (OSError, ValueError):
        raise ValueError("asset_manifest_unavailable") from None
    if manifest != _manifest():
        raise ValueError("asset_manifest_pin_mismatch")
    return {name: verified_bytes(local_path(directory, name), pin.sha256, pin.size) for name, pin in BGE_FILES.items()}


def _download(url: str, target: Path, expected_hash: str, expected_size: int | None) -> None:
    if target.exists():
        verified_bytes(target, expected_hash, expected_size)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    _unlinked(target.parent)
    temporary: Path | None = None
    try:
        # Explicit public bytes only, no ambient proxy/authentication helpers.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with (
            opener.open(url, timeout=120) as response,
            tempfile.NamedTemporaryFile(dir=target.parent, prefix=".download-", delete=False) as output,
        ):
            temporary = Path(output.name)
            maximum = expected_size if expected_size is not None else 5_000_000
            received = 0
            while chunk := response.read(1024 * 1024):
                received += len(chunk)
                if received > maximum:
                    raise ValueError("asset_download_size_exceeded")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        verified_bytes(temporary, expected_hash, expected_size)
        temporary.replace(target)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def prepare_assets(directory: Path) -> Path:
    """Explicit network action for pinned public artifacts; no executable model code."""
    directory.mkdir(parents=True, exist_ok=True)
    for name, pin in BGE_FILES.items():
        target = local_path(directory, name)
        url = f"https://huggingface.co/{EMBEDDING_MODEL}/resolve/{EMBEDDING_REVISION}/{name}"
        _download(url, target, pin.sha256, pin.size)
        if not pin.lfs:
            value = target.read_bytes()
            blob = hashlib.sha1(b"blob " + str(len(value)).encode() + b"\0" + value, usedforsecurity=False).hexdigest()
            if blob != pin.git_blob:
                raise ValueError("asset_git_blob_mismatch")
    _download(O200K_URL, local_path(directory, "o200k_base.tiktoken"), O200K_SHA256, None)
    return write_manifest(directory)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "verify"))
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare_assets(args.directory)
    else:
        verify_assets(args.directory)
    Console().print_json(data={"model_id": EMBEDDING_MODEL, "revision": EMBEDDING_REVISION, "status": "verified"})


if __name__ == "__main__":
    main()
