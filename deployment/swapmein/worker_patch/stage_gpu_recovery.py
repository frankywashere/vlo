"""Stage an undeployed point-recovery overlay without downloading any models."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import tarfile

BASE = "us-east4-docker.pkg.dev/charswap-franky-20261013/proof/gpu@sha256:52f5be6c0bb40e38e5631b49a75d4b95cbd081e51a6e5c45bb9c447a90f4582f"


def stage(output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise ValueError("Use an empty staging directory")
    directory = Path(__file__).parent
    sources = (("bounded_gpu_mask_iou.py", "bounded_gpu_mask_iou.py"),
        ("serve_gpu_recovery.py", "serve_gpu.py"), ("mask_recovery.py", "mask_recovery.py"))
    hashes = {}
    with tarfile.open(output / "gpu_recovery_patch.tar", "w") as archive:
        for name, destination in sources:
            data = (directory / name).read_bytes()
            hashes[name] = hashlib.sha256(data).hexdigest()
            entry = tarfile.TarInfo("app/" + destination)
            entry.size, entry.mode = len(data), 0o644
            archive.addfile(entry, io.BytesIO(data))
    # Reuse the builder pinned by the existing Iowa overlay recipe.
    builder = json.loads((directory / "cloudbuild.gpu-iowa.json").read_text())["steps"][0]["name"]
    recipe = {"steps": [{"name": builder, "args": ["append", "--base", BASE,
        "--new_layer", "gpu_recovery_patch.tar", "--new_tag",
        "us-east4-docker.pkg.dev/charswap-franky-20261013/proof/gpu:point-recovery-v1"]}],
        "timeout": "300s", "options": {"logging": "GCS_ONLY"},
        "logsBucket": "gs://charswap-franky-20261013-proof/build-logs"}
    (output / "cloudbuild.json").write_text(json.dumps(recipe, indent=2))
    manifest = {"base": BASE, "sources": hashes, "deployed": False}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(stage(parser.parse_args().output), indent=2))
