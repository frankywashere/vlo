"""Accepted seed pixels remain on the same label at downstream guide sizes."""
import numpy as np
from PIL import Image
import pytest

from swapmein.meta_seeds import _selection_candidates
from swapmein.tracking import _pixel_label


@pytest.mark.parametrize("size", [(636, 480), (908, 684), (1816, 1368)])
def test_snapped_pixel_center_survives_categorical_resize_for_identity_matching(size):
    mask = np.zeros((480, 636), dtype=bool)
    mask[251:401, 363:501] = True
    mask[252, 363] = False
    selected = _selection_candidates([("person", mask)], (357, 252), 636, 480)
    assert len(selected) == 1
    _, _, (x, y), distance = selected[0]
    assert distance == pytest.approx(6.082762530298219)
    labels = np.asarray(Image.fromarray(mask.astype(np.uint8)).resize(size, Image.Resampling.NEAREST))
    anchor = {"x": (x + .5) / 636, "y": (y + .5) / 480}
    assert _pixel_label(lambda _: labels, 0, anchor, *size) == 1
    assert _pixel_label(lambda _: labels, 0, {"x": 357 / 636, "y": 252 / 480}, *size) == 0
