"""Stage only the CPU identity-depth source overlay on a verified live base.

This command performs no cloud operations. The caller supplies the immutable
currently serving CPU image and a unique build tag after checking worker state.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


REPOSITORY = "us-east4-docker.pkg.dev/charswap-franky-20261013/proof/audio"


def stage(output, base_image, image):
    if not isinstance(base_image, str) or not re.fullmatch(
            re.escape(REPOSITORY) + r"@sha256:[a-f0-9]{64}", base_image):
        raise ValueError("Use the verified immutable CPU image in this project's audio repository")
    if not isinstance(image, str) or not re.fullmatch(
            re.escape(REPOSITORY) + r":[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", image):
        raise ValueError("Use a unique tagged image in this project's audio repository")
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty staging directory")
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(__file__).parent
    # Dependencies, face mesh, its model, and all model layers remain in base.
    for name in ("app.py", "composite.py", "depth_identity_smoke.py"):
        shutil.copyfile(directory / name, output / name)
    (output / "Dockerfile").write_text(
        f"FROM {base_image}\n"
        "COPY app.py composite.py /app/\n"
        "COPY depth_identity_smoke.py /tmp/depth-identity-smoke.py\n"
        "RUN python /tmp/depth-identity-smoke.py && pip check\n"
    )
    config = {
        "steps": [{"name": "gcr.io/cloud-builders/docker",
                   "args": ["build", "-t", image, "."]}],
        "images": [image], "timeout": "1200s",
        "options": {"logging": "GCS_ONLY"},
        "logsBucket": "gs://charswap-franky-20261013-proof/build-logs",
    }
    (output / "cloudbuild.json").write_text(json.dumps(config, indent=2) + "\n")
    manifest = {"base_image": base_image, "image": image,
                "sources": {name: hashlib.sha256((output / name).read_bytes()).hexdigest()
                            for name in ("app.py", "composite.py", "depth_identity_smoke.py", "Dockerfile")}}
    (output / "source-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--image", required=True)
    args = parser.parse_args()
    print(json.dumps(stage(args.output, args.base_image, args.image), indent=2))
