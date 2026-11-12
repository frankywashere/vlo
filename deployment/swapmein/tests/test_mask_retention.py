"""Default production policy keeps assigned SAM shapes without paid rechecks."""
from copy import deepcopy
import zipfile
from io import BytesIO

import numpy as np
import pytest

from swapmein.errors import PublicError
from swapmein.mask_quality import CONTINUITY_CLEANUP_ENABLED, _png, _write
from swapmein.mask_retention import POLICY_VERSION
from swapmein.scene_selection import mask_quality_review_pending
from test_mask_recheck_runtime import example, archive_arrays  # noqa: F401
from test_mask_gap_pipeline import setup_clip


@pytest.mark.parametrize('model', ['minimax-h3', 'kling-4-0'])
@pytest.mark.parametrize('provider', ['meta', 'replicate', 'workers'])
def test_default_pause_preserves_all_assigned_pixels_and_skips_paid_checks(example, monkeypatch, model, provider):
    assert CONTINUITY_CLEANUP_ENABLED is False
    job = setup_clip(example, whole_gaps=True)
    job.update(generation_model=model, sam_provider=provider)
    example.records.data = deepcopy(job)
    monkeypatch.setattr('swapmein.mask_recheck_runtime.recheck_masks',
        lambda *a, **k: pytest.fail('Paused shape cleanup must not buy component rechecks'))
    result = example.pipeline.check_automatic_mask_quality(job, job['tracking_metadata'],
        job['normalized_targets'], 'original', 'vocals', None, lambda: 90)
    report = result['reviewed_mask_quality']
    assert report['policy_version'] == POLICY_VERSION and report['removed_pixels'] == 0
    assert report['sha256'] == report['input_sha256']
    assert result['reviewed_masks_uri'] == 'original'
    assert all(np.array_equal(a, b) for a, b in zip(example.maps,
        archive_arrays(example.cloud.objects[result['reviewed_masks_uri']])) )
    assert not mask_quality_review_pending(result) and not report['review_required']
    assert result['generation_after_review_revision'] == job['generation_after_review_revision']
    assert not example.calls and not example.cloud.enqueued
    downloads = len(example.cloud.downloads)
    assert example.pipeline.check_reviewed_mask_quality(result) == result
    assert len(example.cloud.downloads) == downloads


def test_missing_source_anchor_still_withholds_only_that_identity_for_review(example):
    job = setup_clip(example)
    job['tracking_choices'][0]['anchor']['x'] = .99
    example.records.data = deepcopy(job)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert result['reviewed_mask_quality']['review_required']
    assert result['normalized_targets'][0]['guide_anchor_untrusted']
    assert not result['normalized_targets'][1].get('guide_anchor_untrusted')
    output = archive_arrays(example.cloud.objects[result['reviewed_masks_uri']])
    assert all(not np.any(frame == 1) for frame in output)
    assert all(np.array_equal(before == 2, after == 2) for before, after in zip(example.maps, output))
    assert result['generation_after_review_revision'] is None and not example.calls


def test_pending_component_review_is_not_auto_accepted_by_pause(example):
    job = setup_clip(example)
    job['targeted_mask_review'] = {'pending': True}
    job['tracking_plan']['version'] = 4
    assert example.pipeline.check_reviewed_mask_quality(job) == job
    assert not example.cloud.downloads and not example.calls


@pytest.mark.parametrize('invalid', ['unknown_label', 'absent_label', 'cross_cut_assignment'])
def test_source_identity_and_cut_validation_remains_active(example, invalid):
    job = setup_clip(example)
    if invalid == 'unknown_label':
        example.maps[0][0, 0] = 99
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for frame, labels in enumerate(example.maps):
                _write(archive, f'labels/labels_{frame:05d}.png', _png(labels))
        example.cloud.objects['original'] = buffer.getvalue()
    elif invalid == 'absent_label':
        job['tracking_choices'][0].update(absent=True, anchor=None)
        job['tracking_assignments'][0]['absent'] = True
    else:
        job['tracking_metadata']['scene_cuts'] = [2.0]
    example.records.data = deepcopy(job)
    with pytest.raises(PublicError):
        example.pipeline.check_reviewed_mask_quality(job)
    assert not example.calls and not example.cloud.enqueued


def test_old_geometric_approval_cache_cannot_hide_new_retention_policy(example):
    job = setup_clip(example)
    job['reviewed_mask_quality'] = {'policy_version': 'reviewed_anchor_components_v3',
        'review_revision': 1, 'output_uri': 'original', 'review_required': True,
        'suppressed_ranges': [{'target_id': 'one', 'label': 1, 'start_frame': 42,
            'end_frame': 60, 'reason': 'unresolved_component_continuity'}]}
    example.records.data = deepcopy(job)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert result['reviewed_mask_quality']['policy_version'] == POLICY_VERSION
    assert not result['reviewed_mask_quality']['suppressed_ranges']
    assert not mask_quality_review_pending(result) and not example.calls


def test_explicit_old_fragment_dismissal_is_not_undone_by_the_pause(example, tmp_path):
    from swapmein.mask_quality import sanitize_reviewed_masks
    from swapmein.mask_recheck_plan import plan_rechecks
    from swapmein.mask_recheck_runtime import context_key
    job = deepcopy(example.job)
    source, checked = tmp_path / 'source.zip', tmp_path / 'checked.zip'
    source.write_bytes(example.cloud.objects['original'])
    report = sanitize_reviewed_masks(source, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], checked, review_revision=1)
    candidates = plan_rechecks(source, checked, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], review_revision=1)
    assert candidates
    job['targeted_mask_review'] = {'pending': False,
        'decisions': [{'candidate_id': item['candidate_id'], 'absent': True} for item in candidates]}
    job['mask_identity_recheck_state'] = {'key': context_key(job, report['input_sha256']),
        'status': 'completed', 'candidates': candidates, 'requests': 1,
        'evidence': [{**item, 'outcome': 'uncertain'} for item in candidates]}
    example.records.data = deepcopy(job)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert result['reviewed_mask_quality']['policy_version'] == 'reviewed_anchor_components_v3'
    assert result['targeted_mask_review']['decisions'] == job['targeted_mask_review']['decisions']
    output = archive_arrays(example.cloud.objects[result['reviewed_masks_uri']])
    assert all(not np.any(frame == 1) for frame in output[3:])
    assert not example.calls and not example.cloud.enqueued
