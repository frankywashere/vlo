"""Stage a bounded CPU-only source overlay on the verified face-mesh runtime."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def stage(output):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty staging directory")
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(__file__).parent
    for name in ("app.py", "composite.py", "face_mesh.py", "cyan_overlay_smoke.py"):
        shutil.copyfile(directory / name, output / name)
    shutil.copyfile(directory / "Dockerfile.cyan-overlay", output / "Dockerfile")
    image = "us-east4-docker.pkg.dev/charswap-franky-20261013/proof/audio:cyan20-v8"
    config = {"steps": [{"name": "gcr.io/cloud-builders/docker", "args": ["build", "-t", image, "."]}],
              "images": [image], "timeout": "1200s", "options": {"logging": "GCS_ONLY"},
              "logsBucket": "gs://charswap-franky-20261013-proof/build-logs"}
    (output / "cloudbuild.json").write_text(json.dumps(config, indent=2))
    return {"image": image, "sources": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir() if p.suffix == ".py"}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    print(json.dumps(stage(parser.parse_args().output), indent=2))
