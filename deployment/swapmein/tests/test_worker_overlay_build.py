"""The tiny CPU overlay must include the complete ABI-specific Pillow wheel."""
import hashlib
from io import BytesIO
import importlib.util
from pathlib import Path
import tarfile
import zipfile

import pytest


@pytest.fixture
def builder():
    path = Path(__file__).parents[1] / "worker_patch/stage_cpu_overlay.py"
    spec = importlib.util.spec_from_file_location("cpu_overlay_stage", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture_wheel(builder, extra=None):
    members = {"PIL/__init__.py": b"", "PIL/Image.py": b"", "PIL/_imaging.cpython-312-x86_64-linux-gnu.so": b"ELF",
        "pillow.libs/libjpeg.so": b"ELF", "pillow-11.3.0.dist-info/METADATA": b"Name: pillow\nVersion: 11.3.0\n",
        "pillow-11.3.0.dist-info/WHEEL": b"Tag: cp312-cp312-manylinux_2_27_x86_64\n"}
    members.update(extra or {})
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    return output.getvalue(), members


def test_dependency_staging_keeps_shared_libraries_metadata_and_sources_deterministically(builder, monkeypatch, tmp_path):
    content, members = fixture_wheel(builder)
    monkeypatch.setattr(builder, "PILLOW_SHA256", hashlib.sha256(content).hexdigest())
    wheel = tmp_path / builder.PILLOW_WHEEL
    wheel.write_bytes(content)
    first, second = builder.stage(tmp_path / "one", wheel), builder.stage(tmp_path / "two", wheel)
    assert first["layer_sha256"] == second["layer_sha256"]
    assert first["wheel_members"] == len(members)
    with tarfile.open(tmp_path / "one" / builder.LAYER_NAME) as archive:
        assert set(archive.getnames()) == {"app/app.py", "app/composite.py", "app/common.py"} | {
            builder.SITE + name for name in members}
        assert archive.extractfile(builder.SITE + "pillow.libs/libjpeg.so").read() == b"ELF"
        assert all(item.uid == item.gid == item.mtime == 0 for item in archive)
    with pytest.raises(ValueError, match="empty staging"):
        builder.stage(tmp_path / "one", wheel)


def test_dependency_staging_rejects_wrong_hash_or_unsafe_wheel_paths(builder, monkeypatch):
    content, _ = fixture_wheel(builder)
    with pytest.raises(ValueError, match="SHA256"):
        builder.wheel_entries(content)
    unsafe, _ = fixture_wheel(builder, {"../../outside.py": b"bad"})
    monkeypatch.setattr(builder, "PILLOW_SHA256", hashlib.sha256(unsafe).hexdigest())
    with pytest.raises(ValueError, match="Unsafe"):
        builder.wheel_entries(unsafe)
