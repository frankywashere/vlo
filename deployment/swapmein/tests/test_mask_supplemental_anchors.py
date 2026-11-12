"""An independent confirmed component supplements, never replaces, prior proof."""
from copy import deepcopy
from io import BytesIO
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.mask_quality import sanitize_reviewed_masks
from swapmein.mask_recheck_plan import plan_rechecks
from test_mask_quality import base, state


def fixture(tmp_path):
    archive, metadata, plan, choices, assignments = state(tmp_path)
    maps = [base(), base()]
    maps[1][0:8, 30:40] = 1  # Disconnected body fragment, never inferred by geometry.
    with zipfile.ZipFile(archive, 'w') as out:
        for frame, mask in enumerate(maps):
            data = BytesIO(); Image.fromarray(mask).save(data, 'PNG')
            out.writestr(f'labels/labels_{frame:05d}.png', data.getvalue())
    baseline = tmp_path / 'baseline.zip'
    report = sanitize_reviewed_masks(archive, metadata, plan, choices, assignments, baseline, review_revision=2)
    candidates = plan_rechecks(archive, baseline, metadata, plan, choices, assignments, review_revision=2)
    assert len(candidates) == 1
    return archive, metadata, plan, choices, assignments, baseline, report, candidates, maps


def read(path):
    with zipfile.ZipFile(path) as z:
        return [np.asarray(Image.open(BytesIO(z.read(f'labels/labels_{f:05d}.png')))) for f in range(2)]


def test_confirmed_component_keeps_both_original_and_new_branches(tmp_path):
    archive, metadata, plan, choices, assignments, baseline, _, candidates, maps = fixture(tmp_path)
    before = read(baseline)
    assert not before[1][0:8, 30:40].any()
    output = tmp_path / 'supplemented.zip'
    report = sanitize_reviewed_masks(archive, metadata, plan, choices, assignments, output,
        review_revision=2, supplemental_anchors=candidates)
    after = read(output)
    for original, prior, checked in zip(maps, before, after):
        assert np.array_equal(original, checked)
        assert not ((prior == 1) & (checked != 1)).any()
        assert np.array_equal(prior == 2, checked == 2)
    assert report['removed_pixels'] == 0
    assert report['supplemental_anchor_ids'] == [candidates[0]['candidate_id']]


@pytest.mark.parametrize('corruption', ['hash', 'revision', 'target', 'label', 'frame', 'component', 'duplicate'])
def test_supplemental_binding_cannot_change_saved_pixels_or_revision(tmp_path, corruption):
    archive, metadata, plan, choices, assignments, _, _, candidates, _ = fixture(tmp_path)
    evidence = deepcopy(candidates)
    if corruption == 'hash': evidence[0]['input_sha256'] = '0' * 64
    if corruption == 'revision': evidence[0]['review_revision'] = 1
    if corruption == 'target': evidence[0]['target_id'] = 'two'
    if corruption == 'label': evidence[0]['label'] = 2
    if corruption == 'frame': evidence[0]['frame'] = 0
    if corruption == 'component': evidence[0]['component_sha256'] = '0' * 64
    if corruption == 'duplicate': evidence *= 2
    output = tmp_path / 'preserve.zip'; output.write_bytes(b'keep')
    with pytest.raises(PublicError):
        sanitize_reviewed_masks(archive, metadata, plan, choices, assignments, output,
            review_revision=2, supplemental_anchors=evidence)
    assert output.read_bytes() == b'keep'
    assert not output.with_name(output.name + '.partial').exists()
