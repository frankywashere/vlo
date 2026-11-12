"""Every rejected continuity frame consumes the same bounded uncertainty budget."""
import numpy as np
import pytest

from test_mask_quality import base, run


@pytest.mark.parametrize("backwards", [False, True])
def test_three_rejected_merged_frames_do_not_silently_reacquire_identity(tmp_path, backwards):
    maps = [base() for _ in range(8)]
    rejected = range(4, 7) if backwards else range(1, 4)
    for frame in rejected:
        # A merged foreground component still touches the last accepted body,
        # but is more than twice its area. The other selected person is intact.
        maps[frame][0:64, 0:44] = 1
    anchor = (.3, .35, 7 if backwards else 0)
    report, checked, _ = run(tmp_path, maps, anchor=anchor)
    after_budget = range(0, 4) if backwards else range(4, 8)
    assert all(not np.any(checked[frame] == 1) for frame in after_budget)
    assert all(np.array_equal(checked[frame] == 2, maps[frame] == 2) for frame in range(8))
    assert any(item["reason"] == "merged_component_jump" for item in report["suppressed_ranges"])
    assert any(item["reason"] == "unresolved_component_continuity" for item in report["suppressed_ranges"])
    assert report["review_required"]


@pytest.mark.parametrize("bad_frames", [1, 2])
@pytest.mark.parametrize("backwards", [False, True])
def test_rejected_merge_within_budget_can_return_to_supported_component(tmp_path, bad_frames, backwards):
    maps = [base() for _ in range(8)]
    rejected = range(7 - bad_frames, 7) if backwards else range(1, 1 + bad_frames)
    for frame in rejected:
        maps[frame][0:64, 0:44] = 1
    report, checked, _ = run(tmp_path, maps, anchor=(.3, .35, 7 if backwards else 0))
    resumed = 6 - bad_frames if backwards else 1 + bad_frames
    assert np.array_equal(checked[resumed], maps[resumed])
    assert report["review_required"]


def test_explicit_review_anchor_in_next_scene_resets_exhausted_budget(tmp_path):
    maps = [base() for _ in range(8)]
    for frame in range(1, 4):
        maps[frame][0:64, 0:44] = 1
    report, checked, _ = run(tmp_path, maps, cuts=[4 / 24])
    assert all(np.array_equal(checked[frame], maps[frame]) for frame in range(4, 8))
    assert report["review_required"]


def test_cached_v1_report_is_rechecked_under_corrected_uncertainty_policy(tmp_path):
    from test_mask_quality_pipeline import fixture_job
    from swapmein.mask_quality import POLICY_VERSION, _sha
    job, pipeline, _, cloud = fixture_job(tmp_path)
    original = cloud.objects["original"]
    job["reviewed_mask_quality"] = {"policy_version": "reviewed_anchor_components_v1",
        "review_revision": 1, "output_uri": "original", "sha256": _sha(tmp_path / "input.zip")}
    checked = pipeline.check_reviewed_mask_quality(job)
    assert cloud.downloads == 1
    assert checked["reviewed_mask_quality"]["policy_version"] == POLICY_VERSION
    assert checked["reviewed_mask_quality"]["policy_version"] != "reviewed_anchor_components_v1"
    assert cloud.objects["original"] == original
