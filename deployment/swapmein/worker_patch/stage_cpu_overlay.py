#!/usr/bin/env python3
"""Build a deterministic source + pinned Pillow layer without pulling model images."""
import argparse
import hashlib
from io import BytesIO
import json
from pathlib import Path, PurePosixPath
import shutil
import tarfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
PILLOW_VERSION = "11.3.0"
PILLOW_WHEEL = "pillow-11.3.0-cp312-cp312-manylinux_2_27_x86_64.manylinux_2_28_x86_64.whl"
PILLOW_URL = "https://files.pythonhosted.org/packages/e4/c9/06dd4a38974e24f932ff5f98ea3c546ce3f8c995d3f0985f8e5ba48bba19/" + PILLOW_WHEEL
PILLOW_SHA256 = "676b2815362456b5b3216b4fd5bd89d362100dc6f4945154ff172e206a22c024"
LAYER_NAME = "overlay_cpu_pillow_patch.tar"
SITE = "usr/local/lib/python3.12/site-packages/"


def wheel_entries(content):
    if not 0 < len(content) <= 10_000_000 or hashlib.sha256(content).hexdigest() != PILLOW_SHA256:
        raise ValueError("Pillow wheel failed the pinned official-PyPI SHA256 check")
    roots = {"PIL", "pillow.libs", f"pillow-{PILLOW_VERSION}.dist-info"}
    result = {}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        for item in archive.infolist():
            if item.is_dir():
                continue
            path = PurePosixPath(item.filename)
            if (path.is_absolute() or ".." in path.parts or path.parts[0] not in roots
                    or item.filename in result or item.file_size > 20_000_000
                    or ((item.external_attr >> 16) & 0o170000) == 0o120000):
                raise ValueError("Unsafe or unexpected Pillow wheel member")
            result[item.filename] = archive.read(item)
    required = {"PIL/__init__.py", "PIL/Image.py", f"pillow-{PILLOW_VERSION}.dist-info/METADATA",
                f"pillow-{PILLOW_VERSION}.dist-info/WHEEL"}
    if not required.issubset(result) or not any(name.startswith("pillow.libs/") for name in result):
        raise ValueError("Pillow wheel omits its package, metadata or bundled shared libraries")
    metadata = result[f"pillow-{PILLOW_VERSION}.dist-info/METADATA"].decode()
    wheel = result[f"pillow-{PILLOW_VERSION}.dist-info/WHEEL"].decode()
    if f"Version: {PILLOW_VERSION}\n" not in metadata or "cp312-cp312-manylinux_2_27_x86_64" not in wheel:
        raise ValueError("Pillow wheel is not the pinned CPython 3.12 Linux x86_64 build")
    return result


def stage(output: Path, wheel: Path | None = None):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Use an empty staging directory; existing build files are never replaced")
    if wheel is not None:
        content = wheel.read_bytes()
    else:
        request = urllib.request.Request(PILLOW_URL, headers={"User-Agent": "RecastFX-worker-build/1"})
        with urllib.request.urlopen(request, timeout=60) as response:
            content = response.read(10_000_001)
    entries = wheel_entries(content)
    output.mkdir(parents=True, exist_ok=True)
    sources = {f"app/{name}": (HERE / name).read_bytes() for name in ("app.py", "common.py", "composite.py")}
    members = {**sources, **{SITE + name: data for name, data in entries.items()}}
    # Root-owned regular files and a fixed timestamp make repeated staging of
    # these sources and this wheel produce the same layer digest.
    layer = output / LAYER_NAME
    with tarfile.open(layer, "w", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in sorted(members.items()):
            info = tarfile.TarInfo(name)
            info.size, info.mode, info.uid, info.gid, info.mtime = len(content), 0o644, 0, 0, 0
            info.uname = info.gname = ""
            archive.addfile(info, BytesIO(content))
    manifest = {"pillow_version": PILLOW_VERSION, "wheel": PILLOW_WHEEL,
        "wheel_url": PILLOW_URL, "wheel_sha256": PILLOW_SHA256, "wheel_members": len(entries),
        "site_packages": SITE, "layer": LAYER_NAME,
        "layer_sha256": hashlib.sha256(layer.read_bytes()).hexdigest(),
        "source_sha256": {name: hashlib.sha256(data).hexdigest() for name, data in sources.items()}}
    (output / "dependency-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    shutil.copyfile(HERE / "cloudbuild.json", output / "cloudbuild.json")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--wheel", type=Path, help="Optional cached official wheel; the same SHA256 is still required")
    args = parser.parse_args()
    manifest = stage(args.output, args.wheel)
    print(json.dumps({"output": str(args.output), "layer_sha256": manifest["layer_sha256"],
                      "pillow_version": manifest["pillow_version"]}))


if __name__ == "__main__":
    main()
