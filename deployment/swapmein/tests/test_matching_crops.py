import numpy as np
import pytest

from swapmein.matching_crops import dominant_region, sample_frames


def test_disconnected_specks_do_not_expand_person_crop_or_modify_masks():
    labels = np.zeros((100, 120), np.uint16)
    labels[5:95, 85:115] = 3  # Foreground shoulder at the right edge.
    labels[2, 1] = labels[50, 2] = 3  # Strays next to somebody else's face.
    original = labels.copy()
    region = dominant_region(labels, 3)
    assert region == {"box": (85, 5, 115, 95), "pixels": 2700, "discarded_pixels": 2}
    assert np.array_equal(labels, original)


def test_diagonal_body_pixels_stay_connected_and_other_ids_are_excluded():
    labels = np.diag(np.ones(10, dtype=np.uint16))
    labels[0, 9] = 2
    assert dominant_region(labels, 1)["box"] == (0, 0, 10, 10)
    assert dominant_region(labels, 1)["pixels"] == 10
    assert dominant_region(labels, 3) is None


@pytest.mark.parametrize("start,end", [(0, 1), (4, 6), (88, 291)])
def test_observations_stay_inside_their_own_shot_and_are_distinct(start, end):
    samples = [(frame, {1, 3}) for frame in range(start, end)]
    result = sample_frames(samples, start, end)
    assert len(result) == min(3, end - start)
    assert len(result) == len(set(result))
    assert all(start <= frame < end for frame in result)


def test_transient_track_does_not_force_all_evidence_onto_one_frame():
    samples = [(0, {1}), (1, {1, 3}), (2, {3}), (3, {1, 3})]
    assert len(sample_frames(samples, 0, 4)) == 3
