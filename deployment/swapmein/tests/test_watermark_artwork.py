import numpy as np
from PIL import Image
import pytest

from swapmein.watermark_artwork import create_watermark


@pytest.mark.parametrize("background", ["white", "black", "magenta", "yellow"])
def test_brand_colors_remain_readable_on_light_dark_and_bright_footage(background):
    badge, _ = create_watermark(1280, 720)
    result = Image.alpha_composite(Image.new("RGBA", badge.size, background), badge).convert("RGB")
    pixels = np.asarray(result)
    black = pixels.max(axis=2) < 40
    red = (pixels[:, :, 0] > 170) & (pixels[:, :, 1] < 100) & (pixels[:, :, 2] < 100)
    assert black.sum() > 30 and red.sum() > 10
    assert np.where(red)[1].mean() > np.where(black)[1].mean()
    assert ((pixels.min(axis=2) > 235) & (np.asarray(badge)[:, :, 3] >= 246)).sum() > 100


@pytest.mark.parametrize("dimensions", [(320, 240), (1080, 1920), (1920, 1080), (5760, 3240), (32, 32), (2, 2)])
def test_watermark_scales_for_landscape_portrait_and_small_videos(dimensions):
    width, height = dimensions
    badge, margin = create_watermark(width, height)
    assert badge.mode == "RGBA"
    assert 0 <= margin and badge.width + margin * 2 <= width
    assert badge.height + margin * 2 <= height
    if min(dimensions) >= 240:
        assert badge.width <= width * .35 and badge.height <= height * .15


def test_domain_suffix_is_black_smaller_and_after_the_red_fx():
    badge, _ = create_watermark(1920, 1080)
    pixels = np.asarray(Image.alpha_composite(Image.new("RGBA", badge.size, "white"), badge).convert("RGB"))
    red = (pixels[:, :, 0] > 170) & (pixels[:, :, 1] < 100) & (pixels[:, :, 2] < 100)
    black = pixels.max(axis=2) < 40
    red_start, red_end = np.where(red)[1].min(), np.where(red)[1].max()
    suffix = black[:, red_end + 2:]
    prefix = black[:, :red_start]
    assert suffix.sum() > 25
    assert np.ptp(np.where(suffix)[0]) < np.ptp(np.where(prefix)[0])
