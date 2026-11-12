from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

import swapmein.cloudflare_recovery as recovery
from swapmein.errors import PublicError


def archive(path, maps):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        for frame, labels in enumerate(maps):
            buffer = BytesIO()
            Image.fromarray(labels.astype(np.uint16)).save(buffer, "PNG")
            output.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())


def setup(tmp_path, retry=True):
    labels = np.zeros((8, 8), dtype=np.uint16)
    labels[3:7, 1:3] = 7
    labels[5:7, 5:7] = 300
    labels[0, 0] = 1  # New IDs must not collide with labels anywhere in the clip.
    maps = [labels.copy() for _ in range(48)]
    masks = tmp_path / "raw.zip"
    archive(masks, maps)
    metadata = {"width": 8, "height": 8, "frames": 48, "duration": 2,
                "anchor": {"frame": 0}, "scene_cuts": [1]}
    targets = [{"id": "known", "reference_index": 0, "anchor": {"x": .2, "y": .6, "time": .25}},
               {"id": "missing", "reference_index": 1, "anchor": {"x": .8, "y": .2, "time": 1.5}}]
    job = {"id": "a" * 32, "retry_missing_masks_with_sam": retry}
    objects, uploads = {}, []
    class Cloud:
        def upload(self, bucket, key, path, mime):
            result = "gs://" + bucket + "/" + key
            objects[result] = Path(path).read_bytes()
            uploads.append((result, mime))
            return result
        def download(self, uri, path, maximum):
            assert maximum == 80 * 1024 * 1024
            path.write_bytes(objects[uri])
    class Records:
        def read(self, path):
            return {**job, "normalized_uri": "gs://normalized-rgb"}, 1
    def update_job(job_id, **fields):
        assert job_id == job['id']
        job.update(fields)
        return dict(job)
    pipeline = SimpleNamespace(cloud=Cloud(), records=Records(), settings=SimpleNamespace(worker_bucket="worker"), update_job=update_job)
    return pipeline, job, masks, metadata, targets, maps, objects, uploads


def test_retry_off_or_no_missing_tap_does_not_activate_any_worker(tmp_path, monkeypatch):
    pipeline, job, masks, metadata, targets, _, _, uploads = setup(tmp_path, retry=False)
    monkeypatch.setattr(recovery, "run_mask_recovery", lambda *args: pytest.fail("No GPU call"))
    assert recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60) is None
    job["retry_missing_masks_with_sam"] = True
    assert recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets[:1], tmp_path, lambda: 60) is None
    assert uploads == []


@pytest.mark.parametrize("depth_provider", ["workers", "replicate"])
def test_meta_initial_miss_requires_review_before_upload_or_extra_paid_recovery(tmp_path, monkeypatch, depth_provider):
    pipeline, job, masks, metadata, targets, _, objects, uploads = setup(tmp_path)
    job.update(sam_provider="meta", preparation_provider=depth_provider, matching_no_review=True)
    original = masks.read_bytes()
    monkeypatch.setattr(recovery, "run_mask_recovery", lambda *args: pytest.fail("Initial Meta budget must not start another paid batch"))
    with pytest.raises(PublicError) as error:
        recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60)
    assert error.value.code == "meta_initial_anchor_review"
    assert masks.read_bytes() == original
    assert uploads == [] and objects == {}
    assert not job.get("identity_anchor_recovery_attempted")
    assert not list(tmp_path.glob("identity-recovery-*"))


@pytest.mark.parametrize("outcome", ["recovered", "conflict", "coordinate_fallback"])
def test_existing_worker_receives_empty_labels_and_merge_preserves_every_raw_id(tmp_path, monkeypatch, outcome):
    pipeline, job, masks, metadata, targets, maps, objects, _ = setup(tmp_path)
    original_bytes = masks.read_bytes()
    def run(pipeline_arg, recovery_job, requests, remaining):
        assert job['identity_anchor_recovery_attempted'] is True
        assert pipeline_arg is pipeline
        assert recovery_job["normalized_uri"] == "gs://normalized-rgb"
        assert recovery_job["recovery_entry_point"] == "automatic_identity_anchor"
        assert requests == [{"segment_id": "identity-initial-missing", "start_frame": 24,
            "end_frame": 48, "frame": 36, "x": .8, "y": .2, "label": 1}]
        with zipfile.ZipFile(BytesIO(objects[recovery_job["reviewed_masks_uri"]])) as worker_input:
            assert all(np.max(np.array(Image.open(BytesIO(worker_input.read(name))))) == 0 for name in worker_input.namelist())
        result_maps = [np.zeros_like(labels) for labels in maps]
        for frame in range(24, 48):
            result_maps[frame][1:3, 5:7] = 1
        result = tmp_path / "worker-result.zip"
        archive(result, result_maps)
        objects["gs://recovered"] = result.read_bytes()
        return "gs://recovered", [], [{"segment_id": requests[0]["segment_id"], "outcome": outcome}]
    monkeypatch.setattr(recovery, "run_mask_recovery", run)
    if outcome != "recovered":
        with pytest.raises(PublicError, match="safely"):
            recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60)
        assert masks.read_bytes() == original_bytes
        return
    output, uri, report = recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60)
    assert output != masks and masks.read_bytes() == original_bytes and objects[uri] == output.read_bytes()
    with zipfile.ZipFile(output) as repaired:
        for frame, before in enumerate(maps):
            after = np.array(Image.open(BytesIO(repaired.read(f"labels/labels_{frame:05d}.png"))))
            assert np.array_equal(after[before != 0], before[before != 0])
            assert np.all(after[1:3, 5:7] == (2 if frame >= 24 else 0))
    assert report[0]["outcome"] == "recovered"


@pytest.mark.parametrize("invalid", ["overlap", "outside_scene", "wrong_label", "missed_tap"])
def test_bad_recovered_pixels_never_replace_original_archive(tmp_path, monkeypatch, invalid):
    pipeline, job, masks, metadata, targets, maps, objects, uploads = setup(tmp_path)
    original_bytes = masks.read_bytes()
    def run(*args):
        additions = [np.zeros_like(labels) for labels in maps]
        for frame in range(24, 48):
            additions[frame][1:3, 5:7] = 1
        if invalid == "overlap":
            additions[36][3, 1] = 1
        elif invalid == "outside_scene":
            additions[0][1, 6] = 1
        elif invalid == "wrong_label":
            additions[36][1, 6] = 9
        elif invalid == "missed_tap":
            additions[36][1, 6] = 0
        result = tmp_path / "worker-result.zip"
        archive(result, additions)
        objects["gs://recovered"] = result.read_bytes()
        return "gs://recovered", [], [{"segment_id": "identity-initial-missing", "outcome": "recovered"}]
    monkeypatch.setattr(recovery, "run_mask_recovery", run)
    with pytest.raises(PublicError, match="safely"):
        recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60)
    assert masks.read_bytes() == original_bytes
    assert not any(uri.endswith("raw-repaired-masks.zip") for uri, _ in uploads)
    assert not list(tmp_path.glob("identity-anchor-repaired-*"))


def test_multiple_missing_people_receive_unique_labels_unused_anywhere_in_raw_clip(tmp_path, monkeypatch):
    pipeline, job, masks, metadata, targets, maps, objects, _ = setup(tmp_path)
    maps[0][0, 1] = 2  # Even a label seen in a different shot cannot be recycled.
    archive(masks, maps)
    targets.append({"id": "also-missing", "reference_index": 2, "anchor": {"x": .5, "y": .2, "time": 1.75}})
    def run(pipeline_arg, recovery_job, requests, remaining):
        assert [item["label"] for item in requests] == [1, 2]
        additions = [np.zeros_like(labels) for labels in maps]
        for frame in range(24, 48):
            additions[frame][1:3, 5:7] = 1
            additions[frame][1:3, 3:5] = 2
        result = tmp_path / "worker-result.zip"
        archive(result, additions)
        objects["gs://recovered"] = result.read_bytes()
        return "gs://recovered", [], [{"segment_id": item["segment_id"], "outcome": "recovered"} for item in requests]
    monkeypatch.setattr(recovery, "run_mask_recovery", run)
    output, _, _ = recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60)
    with zipfile.ZipFile(output) as result:
        labels = np.array(Image.open(BytesIO(result.read("labels/labels_00042.png"))))
    assert np.all(labels[1:3, 5:7] == 3) and np.all(labels[1:3, 3:5] == 4)


def containment_setup(tmp_path, monkeypatch):
    pipeline, job, masks, metadata, targets, maps, objects, uploads = setup(tmp_path)
    # The second shot contains one person, whose jacket was found but face tap
    # missed. An ID reused in a different shot is not a cross-cut identity cue.
    for frame in range(24, 48):
        maps[frame][:] = 0
        maps[frame][5:7, 5:7] = 300
    archive(masks, maps)
    additions = [np.zeros_like(labels) for labels in maps]
    for frame in range(24, 48):
        additions[frame][1:7, 5:7] = 1

    def run(*args):
        result = tmp_path / "worker-result.zip"
        archive(result, additions)
        objects["gs://recovered"] = result.read_bytes()
        return "gs://recovered", [], [{"segment_id": "identity-initial-missing", "outcome": "recovered"}]
    monkeypatch.setattr(recovery, "run_mask_recovery", run)
    return pipeline, job, masks, metadata, targets, maps, additions, objects, uploads


def test_single_person_partial_mask_extension_keeps_original_pixels_and_id(tmp_path, monkeypatch):
    pipeline, job, masks, metadata, targets, maps, additions, objects, _ = containment_setup(tmp_path, monkeypatch)
    original_bytes = masks.read_bytes()
    output, uri, _ = recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60)
    assert masks.read_bytes() == original_bytes and objects[uri] == output.read_bytes()
    with zipfile.ZipFile(output) as repaired:
        for frame, before in enumerate(maps):
            after = np.array(Image.open(BytesIO(repaired.read(f"labels/labels_{frame:05d}.png"))))
            assert np.array_equal(after[before != 0], before[before != 0])
            assert np.all(after[additions[frame] == 1] == 300)
            assert np.all(after[(before == 0) & (additions[frame] == 0)] == 0)
    assert job["identity_anchor_recovery_merge_report"] == [{
        "segment_id": "identity-initial-missing", "policy": "single_person_containment_v1",
        "source_label": 300, "evidence_frames": 24, "minimum_original_containment": 1,
        "minimum_recovery_overlap": pytest.approx(1 / 3), "minimum_main_component_share": 1}]


@pytest.mark.parametrize("invalid", ["another_raw_person", "swallows_second_person", "other_selected_anchor",
                                     "partial_containment", "disconnected_body", "little_overlap",
                                     "missing_recovery_frame", "recovery_without_source", "single_frame"])
def test_ambiguous_containment_still_requires_review(tmp_path, monkeypatch, invalid):
    pipeline, job, masks, metadata, targets, maps, additions, _, uploads = containment_setup(tmp_path, monkeypatch)
    if invalid == "another_raw_person":
        maps[36][0, 0] = 400  # A person outside the recovery also prevents merging.
    elif invalid == "swallows_second_person":
        maps[36][3, 6] = 400
    elif invalid == "other_selected_anchor":
        targets[0]["anchor"] = {"x": .8, "y": .7, "time": 1.5}
    elif invalid == "partial_containment":
        maps[36][7, 6] = 300
    elif invalid == "disconnected_body":
        additions[36][3, 5:7] = 0
    elif invalid == "little_overlap":
        for frame in range(24, 48):
            additions[frame][1:7, :] = 1
    elif invalid == "missing_recovery_frame":
        additions[35][:] = 0
    elif invalid == "recovery_without_source":
        maps[35][:] = 0
    elif invalid == "single_frame":
        for frame in range(24, 48):
            if frame != 36:
                maps[frame][:] = 0
                additions[frame][:] = 0
    archive(masks, maps)
    original_bytes = masks.read_bytes()
    with pytest.raises(PublicError, match="safely"):
        recovery.recover_identity_anchors(pipeline, job, masks, metadata, targets, tmp_path, lambda: 60)
    assert masks.read_bytes() == original_bytes
    assert not any(uri.endswith("raw-repaired-masks.zip") for uri, _ in uploads)
    assert not list(tmp_path.glob("identity-anchor-repaired-*"))


def test_component_evidence_does_not_accept_tap_on_tiny_disconnected_island():
    region = np.zeros((100, 100), dtype=bool)
    region[10:90, 10:90] = True
    region[0, 0] = True
    source = np.zeros_like(region)
    source[40:90, 10:90] = True
    main, overlap, anchor = recovery._component_evidence(region, source, (0, 0))
    assert main == 6400 and overlap == 4000 and not anchor
    assert main / np.count_nonzero(region) > .999


def test_almost_complete_containment_preserves_original_fringe_without_relabeling():
    source = np.zeros((100, 100), dtype=np.uint16)
    source[40:90, 10:90] = 9
    source[90, 10:40] = 9  # Below 1% jacket fringe remains outside the recovery.
    recovered = np.zeros_like(source)
    recovered[10:90, 10:90] = 1
    metadata = {"width": 100, "height": 100, "frames": 3, "duration": .125, "anchor": {"frame": 0}}
    request = {"segment_id": "identity-initial-one", "start_frame": 0, "end_frame": 3,
               "frame": 0, "x": .5, "y": .2, "label": 1}
    labels, report = recovery._contained_overlap_labels(lambda frame: source, lambda frame: recovered,
        metadata, [request], [{"anchor": {"x": .5, "y": .2, "time": 0}}])
    assert labels == {1: 9}
    assert report[0]["minimum_original_containment"] == pytest.approx(4000 / 4030)
