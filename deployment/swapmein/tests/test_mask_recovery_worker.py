"""Scene-bounded point recovery preserves unrelated people, timing and choices."""
import importlib.util
from io import BytesIO
from pathlib import Path
import sys
import zipfile

import numpy as np
from PIL import Image
import pytest


@pytest.fixture
def recovery():
    path = Path(__file__).parents[1] / "worker_patch/mask_recovery.py"
    spec = importlib.util.spec_from_file_location("worker_mask_recovery_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def save(path, maps):
    with zipfile.ZipFile(path, "w") as archive:
        for frame, labels in enumerate(maps):
            image = BytesIO()
            Image.fromarray(labels.astype(np.uint16)).save(image, "PNG")
            archive.writestr(f"labels/labels_{frame:05d}.png", image.getvalue())


def load(path):
    with zipfile.ZipFile(path) as archive:
        return [np.array(Image.open(BytesIO(archive.read(name)))) for name in sorted(archive.namelist())]


def selection(recovery, **changes):
    return recovery.RecoverySelection(**{**dict(segment_id="scene-six-person-two", start_frame=1,
        end_frame=3, frame=2, label=2, x=.75, y=.5), **changes})


def test_recovery_only_tracks_requested_scene_and_retains_other_target(recovery, tmp_path):
    source, output = tmp_path / "source.zip", tmp_path / "output.zip"
    maps = [np.pad(np.ones((4, 2), dtype=np.uint16), ((0, 0), (0, 2))) for _ in range(4)]
    save(source, maps)
    frames = [np.full((4, 4, 3), i, dtype=np.uint8) for i in range(4)]
    def track(scene, tap, x, y):
        assert [frame[0, 0, 0] for frame in scene] == [1, 2]
        assert (tap, x, y) == (1, .75, .5)
        return {i: np.pad(np.ones((4, 2), dtype=bool), ((0, 0), (2, 0))) for i in range(2)}
    report = recovery.recover_archive(source, output, frames, [selection(recovery)], track)
    assert report["selections"][0]["outcome"] == "recovered"
    result = load(output)
    assert np.array_equal(result[0], maps[0]) and np.array_equal(result[3], maps[3])
    assert all(np.all(result[i][:, :2] == 1) and np.all(result[i][:, 2:] == 2) for i in (1, 2))
    assert report["coverage"] == [8, 16, 16, 8]


@pytest.mark.parametrize("outcome", ["conflict", "coordinate_fallback"])
def test_recovery_never_erases_another_person_or_accepts_a_mask_missing_the_tap(recovery, tmp_path, outcome):
    source, output = tmp_path / "source.zip", tmp_path / "output.zip"
    maps = [np.ones((4, 4), dtype=np.uint16) for _ in range(4)]
    save(source, maps)
    frames = [np.zeros((4, 4, 3), dtype=np.uint8) for _ in maps]
    track = lambda *args: {i: np.full((4, 4), outcome == "conflict", dtype=bool) for i in range(2)}
    report = recovery.recover_archive(source, output, frames, [selection(recovery)], track)
    assert report["selections"][0]["outcome"] == outcome
    assert all(np.array_equal(before, after) for before, after in zip(maps, load(output)))


def test_recovery_rejects_missing_frames_and_invalid_tap_ranges(recovery, tmp_path):
    source = tmp_path / "source.zip"
    save(source, [np.zeros((4, 4), dtype=np.uint16)] * 4)
    with pytest.raises(ValueError, match="incomplete frame timeline"):
        recovery.recover_archive(source, tmp_path / "out.zip", [np.zeros((4, 4, 3))] * 4,
            [selection(recovery)], lambda *args: {})
    with pytest.raises(ValueError, match="inside its saved scene"):
        selection(recovery, frame=3)
    with pytest.raises(ValueError):
        selection(recovery, x=float("nan"))
    with pytest.raises(ValueError, match="five-person clip budget"):
        recovery.RecoveryRequest(input_uri="x", masks_uri="y", run_id="test",
            selections=[selection(recovery)] * 2)


def test_recovery_overlay_is_deterministic_and_contains_its_complete_bootstrap(tmp_path):
    import tarfile
    path = Path(__file__).parents[1] / "worker_patch/stage_gpu_recovery.py"
    spec = importlib.util.spec_from_file_location("stage_gpu_recovery_test", path)
    stage = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stage)
    assert stage.stage(tmp_path / "one") == stage.stage(tmp_path / "two")
    assert (tmp_path / "one/gpu_recovery_patch.tar").read_bytes() == (tmp_path / "two/gpu_recovery_patch.tar").read_bytes()
    with tarfile.open(tmp_path / "one/gpu_recovery_patch.tar") as archive:
        assert set(archive.getnames()) == {"app/serve_gpu.py", "app/mask_recovery.py", "app/bounded_gpu_mask_iou.py"}
        assert b"install_mask_recovery(worker)" in archive.extractfile("app/serve_gpu.py").read()
    with pytest.raises(ValueError, match="empty staging"):
        stage.stage(tmp_path / "one")
