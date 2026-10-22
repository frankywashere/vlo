"""SAM continuity diagnostics observe masks without changing routing or pixels."""
from io import BytesIO
import json
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.tracking import log_track_diagnostics


def masks(path: Path, arrays):
    with zipfile.ZipFile(path, "w") as archive:
        for index, array in enumerate(arrays):
            buffer = BytesIO()
            Image.fromarray(array).save(buffer, "PNG")
            archive.writestr(f"labels/labels_{index:05d}.png", buffer.getvalue())
    return path


def person(label=2, *, x=4, y=4, size=8):
    array = np.zeros((64, 64), dtype=np.uint16)
    array[y:y + size, x:x + size] = label
    return array


def metadata(frames, *, cuts=()):
    return {"width": 64, "height": 64, "frames": frames, "duration": frames / 24,
            "anchor": {"x": .1, "y": .1, "frame": 0, "time": 0},
            "scene_cuts": [frame / 24 for frame in cuts]}


def events(capsys, name):
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return [record for record in records if record.get("event") == name]


def test_jump_logs_normalized_coordinates_and_area_without_changing_archive(tmp_path, capsys):
    path = masks(tmp_path / "masks.zip", [person(), person(x=48, y=40)])
    original = path.read_bytes()

    summary = log_track_diagnostics(path, metadata(2), job_id="diagnostic-job")

    assert path.read_bytes() == original
    assert summary["jump_count"] == 1
    assert summary["anomaly_count"] == 1
    assert summary["labels"] == 1
    [event] = events(capsys, "sam_track_anomaly")
    assert event["job_id"] == "diagnostic-job"
    assert event["diagnostic_only"] is True
    assert event["reason"] == "tracking_jump"
    assert event["label"] == 2
    assert event["frame"] == 1
    assert event["time_seconds"] == pytest.approx(1 / 24, abs=1e-6)
    assert event["scene"] == 0
    assert event["previous_coordinates"] == pytest.approx({"x": 7.5 / 64, "y": 7.5 / 64})
    assert event["coordinates"] == pytest.approx({"x": 51.5 / 64, "y": 43.5 / 64})
    assert event["previous_area_pixels"] == event["area_pixels"] == 64


def test_missing_run_and_reappearance_log_once_each_with_location(tmp_path, capsys):
    absent = np.zeros((64, 64), dtype=np.uint16)
    path = masks(tmp_path / "masks.zip", [person(), absent, absent, person()])

    summary = log_track_diagnostics(path, metadata(4), job_id="diagnostic-job")

    assert summary["jump_count"] == 0
    assert summary["missing_count"] == summary["reappeared_count"] == 1
    assert summary["anomaly_count"] == summary["logged_anomaly_count"] == 2
    anomalies = events(capsys, "sam_track_anomaly")
    assert [(event["reason"], event["frame"]) for event in anomalies] == [
        ("missing_mask", 1), ("tracking_reappeared", 3)]
    missing, reappeared = anomalies
    assert missing["coordinates"] is None
    assert missing["previous_coordinates"] == pytest.approx({"x": 7.5 / 64, "y": 7.5 / 64})
    assert missing["previous_area_pixels"] == 64
    assert missing["area_pixels"] == 0
    assert reappeared["coordinates"] == pytest.approx({"x": 7.5 / 64, "y": 7.5 / 64})
    assert reappeared["area_pixels"] == 64


def test_multiple_scene_clip_is_skipped_without_comparing_across_cut(tmp_path, capsys):
    path = masks(tmp_path / "masks.zip", [person(), person(x=48, y=40)])

    summary = log_track_diagnostics(path, metadata(2, cuts=[1]))

    assert summary == {"skipped": True, "reason": "multiple_scenes"}
    assert not events(capsys, "sam_track_anomaly")


def test_ordinary_motion_logs_completion_without_anomalies(tmp_path, capsys):
    path = masks(tmp_path / "masks.zip", [person(x=4 + index) for index in range(4)])

    summary = log_track_diagnostics(path, metadata(4), job_id="diagnostic-job")

    assert summary == {"frames": 4, "labels": 1, "jump_count": 0,
                       "missing_count": 0, "reappeared_count": 0, "anomaly_count": 0,
                       "logged_anomaly_count": 0, "truncated": False}
    [event] = events(capsys, "sam_track_diagnostics_completed")
    assert event["job_id"] == "diagnostic-job"
    for key, value in summary.items():
        assert event[key] == value


def test_each_sam_label_is_observed_independently(tmp_path, capsys):
    first = person()
    first[24:32, 4:12] = 9
    second = person()
    second[24:32, 48:56] = 9
    path = masks(tmp_path / "masks.zip", [first, second])

    summary = log_track_diagnostics(path, metadata(2))

    assert summary["labels"] == 2
    assert summary["jump_count"] == 1
    [event] = events(capsys, "sam_track_anomaly")
    assert event["label"] == 9


def test_first_appearance_does_not_report_reappearance(tmp_path, capsys):
    absent = np.zeros((64, 64), dtype=np.uint16)
    path = masks(tmp_path / "masks.zip", [absent, absent, person(), person()])

    summary = log_track_diagnostics(path, metadata(4))

    assert summary["labels"] == 1
    assert summary["anomaly_count"] == 0
    assert not events(capsys, "sam_track_anomaly")


def test_anomaly_log_limit_keeps_total_counts(tmp_path, capsys):
    path = masks(tmp_path / "masks.zip", [person(x=4 if frame % 2 == 0 else 48)
                                         for frame in range(70)])

    summary = log_track_diagnostics(path, metadata(70))

    assert summary["jump_count"] == summary["anomaly_count"] == 69
    assert summary["logged_anomaly_count"] == 60
    assert summary["truncated"] is True
    assert len(events(capsys, "sam_track_anomaly")) == 60


@pytest.mark.parametrize("invalid", ["not-zip", "missing-frame", "bad-png"])
def test_malformed_archive_logs_failure_without_raising_or_modifying_input(tmp_path, capsys, invalid):
    path = tmp_path / "masks.zip"
    if invalid == "not-zip":
        path.write_bytes(b"not a zip file")
    elif invalid == "missing-frame":
        masks(path, [person()])
    else:
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("labels/labels_00000.png", b"not an image")
            archive.writestr("labels/labels_00001.png", b"not an image")
    original = path.read_bytes()

    assert log_track_diagnostics(path, metadata(2), job_id="diagnostic-job") is None

    assert path.read_bytes() == original
    [event] = events(capsys, "sam_track_diagnostics_failed")
    assert event["job_id"] == "diagnostic-job"
    assert event["error_type"]
