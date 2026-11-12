"""Confirmed identity permits missing guide tint, never guessed mask pixels."""
from copy import deepcopy
from io import BytesIO
import zipfile

import numpy as np
import pytest

from swapmein.mask_quality import _png, _write, sanitize_reviewed_masks
from swapmein.mask_recheck_plan import plan_rechecks
from swapmein.mask_recheck_runtime import context_key, quality_context_key
from swapmein.scene_selection import mask_quality_review_pending
from test_mask_recheck_runtime import example, archive_arrays  # noqa: F401


def setup_clip(example, *, whole_gaps=False, confirmed=True):
    """A stable person, optional unrelated fragment and two real shot gaps."""
    frames, width, height = (300 if whole_gaps else 120), 160, 96
    boundaries = [0, 98, 220, frames] if whole_gaps else [0, frames]
    maps = []
    for frame in range(frames):
        labels = np.zeros((height, width), np.uint16)
        x = 65 if whole_gaps and (42 <= frame < 98 or 202 <= frame < 220) else 20
        labels[10:85, x:x + 30] = 1
        labels[10:85, 110:145] = 2
        if not whole_gaps and 30 <= frame < 40:
            labels[35:55, 65:75] = 1
        maps.append(labels)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for frame, labels in enumerate(maps):
            _write(archive, f'labels/labels_{frame:05d}.png', _png(labels))
    example.cloud.objects['original'] = buffer.getvalue()
    example.maps = maps
    targets = [dict(id='one', name='Person 1', reference_index=0,
                    anchor=dict(x=30.5 / width, y=40.5 / height, time=0)),
               dict(id='two', name='Person 2', reference_index=1,
                    anchor=dict(x=125.5 / width, y=40.5 / height, time=0))]
    sections, choices, assignments = [], [], []
    for index, target in enumerate(targets):
        target['scenes'] = []
        for shot, (start, end) in enumerate(zip(boundaries, boundaries[1:])):
            anchor = {**target['anchor'], 'time': start / 24}
            segment = f't{index}-s{shot}'
            sections.append(dict(id=segment, target_id=target['id'], start_frame=start,
                end_frame=end, start=start / 24, end=end / 24, needs_review=True))
            choices.append(dict(segment_id=segment, absent=False, anchor=anchor))
            assignments.append(dict(segment_id=segment, target_id=target['id'], start_frame=start,
                end_frame=end, selected_label=index + 1, absent=False))
            target['scenes'].append(dict(start=start / 24, end=end / 24, absent=False, anchor=anchor))
    job = {**deepcopy(example.job), 'identity_matching_completed': confirmed,
        'mask_identity_provenance': 'source_identity_match',
        'tracking_reviewed': True,
        'tracking_metadata': dict(width=width, height=height, frames=frames,
            duration=frames / 24, anchor={**targets[0]['anchor'], 'frame': 0},
            scene_cuts=[frame / 24 for frame in boundaries[1:-1]]),
        'tracking_plan': dict(version=2, targets=deepcopy(targets), segments=sections),
        'tracking_choices': choices, 'tracking_assignments': assignments,
        'normalized_targets': targets}
    example.records.data = deepcopy(job)
    return job


def fail_recheck_credentials(monkeypatch):
    monkeypatch.setattr('swapmein.cloudflare_credentials.server_cloudflare_credentials',
        lambda: pytest.fail('A tolerated omission must not request another identity check'))


@pytest.mark.parametrize('model', ['minimax-h3', 'kling-4-0'])
@pytest.mark.parametrize('provider', ['meta', 'replicate', 'workers'])
def test_initial_automatic_partial_omission_has_no_extra_requests_or_review(example, monkeypatch, model, provider):
    source = setup_clip(example)
    job = {**source, 'generation_model': model, 'sam_provider': provider}
    example.records.data = deepcopy(job)
    fail_recheck_credentials(monkeypatch)
    result = example.pipeline.check_automatic_mask_quality(job, job['tracking_metadata'],
        job['normalized_targets'], 'original', 'vocals', None, lambda: 90)
    report = result['reviewed_mask_quality']
    assert report['gap_tolerance']['forgiven_candidate_ids']
    assert report['identity_rechecks']['requests'] == 0
    assert not report['review_required'] and not mask_quality_review_pending(result)
    assert not result.get('targeted_mask_review') and not example.calls
    output = archive_arrays(example.cloud.objects[result['reviewed_masks_uri']])
    assert not np.any(output[35][35:55, 65:75])
    for original, checked in zip(example.maps, output):
        assert np.array_equal(original == 2, checked == 2)
        assert not np.any((checked != 0) & (checked != original))
    assert not example.cloud.compositions and not example.cloud.enqueued
    assert result['generation_after_review_revision'] == 1  # Preserve authorization; do not invent it.


@pytest.mark.parametrize('model', ['minimax-h3', 'kling-4-0'])
def test_two_brief_whole_gaps_retain_prompt_guidance_without_blocking(example, monkeypatch, model):
    job = setup_clip(example, whole_gaps=True)
    job['generation_model'] = model
    example.records.data = deepcopy(job)
    fail_recheck_credentials(monkeypatch)
    result = example.pipeline.check_reviewed_mask_quality(job)
    report = result['reviewed_mask_quality']
    assert [(item['start_frame'], item['end_frame']) for item in report['suppressed_ranges']] == [(42, 98), (202, 220)]
    assert not report['blocking_suppressed_ranges']
    assert not report['review_required'] and not mask_quality_review_pending(result)
    assert all(not scene.get('guide_anchor_untrusted') for target in result['normalized_targets'] for scene in target['scenes'])
    scenes = result['normalized_targets'][0]['scenes']
    assert scenes[0]['guide_mask_suppressed_ranges'] and scenes[1]['guide_mask_suppressed_ranges']
    output = archive_arrays(example.cloud.objects[result['reviewed_masks_uri']])
    assert all(not np.any(output[frame] == 1) for frame in [42, 70, 97, 202, 219])
    assert not example.calls and not example.cloud.enqueued


@pytest.mark.parametrize('prior_status', ['started', 'completed'])
def test_old_identity_receipt_is_not_retried_or_used_to_restore_forgiven_pixels(example, monkeypatch, prior_status, tmp_path):
    job = setup_clip(example)
    raw, checked = tmp_path / 'raw.zip', tmp_path / 'checked.zip'
    raw.write_bytes(example.cloud.objects['original'])
    report = sanitize_reviewed_masks(raw, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], checked, review_revision=1)
    candidates = plan_rechecks(raw, checked, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], review_revision=1)
    key = context_key(job, report['input_sha256'])
    job['mask_identity_recheck_state'] = dict(key=key, status=prior_status, candidates=candidates,
        evidence=[{**candidate, 'outcome': 'confirmed'} for candidate in candidates], requests=1)
    job['mask_supplemental_context_key'] = key
    job['mask_supplemental_anchors'] = deepcopy(candidates)
    example.records.data = deepcopy(job)
    fail_recheck_credentials(monkeypatch)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert result['mask_identity_recheck_state'] == job['mask_identity_recheck_state']
    assert result['mask_supplemental_anchors'] == []
    assert not result['reviewed_mask_quality']['review_required']
    assert not np.any(archive_arrays(example.cloud.objects[result['reviewed_masks_uri']])[35][35:55, 65:75])
    receipt = result['reviewed_mask_quality']['identity_rechecks']
    assert receipt['requests'] == (None if prior_status == 'started' else 1)
    assert not example.calls


def test_explicit_manual_component_remains_in_output_and_fresh_approval_stays_required(example, monkeypatch, tmp_path):
    job = setup_clip(example)
    raw, checked = tmp_path / 'raw.zip', tmp_path / 'checked.zip'
    raw.write_bytes(example.cloud.objects['original'])
    report = sanitize_reviewed_masks(raw, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], checked, review_revision=1)
    candidates = plan_rechecks(raw, checked, job['tracking_metadata'], job['tracking_plan'],
        job['tracking_choices'], job['tracking_assignments'], review_revision=1)
    job['mask_supplemental_context_key'] = context_key(job, report['input_sha256'])
    job['mask_supplemental_anchors'] = [{**candidate, 'confirmation': 'explicit_source_review'} for candidate in candidates]
    example.records.data = deepcopy(job)
    fail_recheck_credentials(monkeypatch)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert result['mask_supplemental_anchors'] == job['mask_supplemental_anchors']
    assert result['reviewed_mask_quality']['review_required']
    output = archive_arrays(example.cloud.objects[result['reviewed_masks_uri']])
    assert all(np.array_equal(a, b) for a, b in zip(example.maps, output))
    assert result['generation_after_review_revision'] is None
    assert not example.calls and not example.cloud.enqueued


def test_new_quality_policy_does_not_change_paid_identity_context(example):
    job = setup_clip(example)
    identity = context_key(job, 'abc')
    assert quality_context_key(job, 'abc') != identity
    changed = {**job, 'identity_matching_completed': False}
    assert context_key(changed, 'abc') == identity
    assert quality_context_key(changed, 'abc') != quality_context_key(job, 'abc')
    assert quality_context_key({**job, 'tracking_reviewed': False}, 'abc') == quality_context_key(job, 'abc')


def test_unconfirmed_source_identity_still_requires_targeted_check(example, monkeypatch):
    job = setup_clip(example, confirmed=False)
    submitted = []
    def uncertain(video, masks, metadata, candidates, **kwargs):
        submitted.extend(candidates)
        return dict(evidence=[{**candidate, 'outcome': 'uncertain'} for candidate in candidates], requests=len(candidates))
    monkeypatch.setattr('swapmein.mask_identity_rechecks.recheck_mask_identities', uncertain)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert submitted and result['reviewed_mask_quality']['review_required']
    assert result['targeted_mask_review']['pending']
    assert result['reviewed_mask_quality']['gap_tolerance']['global_blockers'] == ['identity_not_confirmed']
    assert result['generation_after_review_revision'] is None
    assert not example.cloud.enqueued


def test_missing_source_anchor_cannot_be_forgiven_by_good_other_frames(example, monkeypatch):
    job = setup_clip(example)
    # The source tap lies on background; no reliable identity anchor exists.
    job['tracking_choices'][0]['anchor']['x'] = 0
    job['normalized_targets'][0]['anchor']['x'] = 0
    job['normalized_targets'][0]['scenes'][0]['anchor']['x'] = 0
    example.records.data = deepcopy(job)
    fail_recheck_credentials(monkeypatch)
    result = example.pipeline.check_reviewed_mask_quality(job)
    report = result['reviewed_mask_quality']
    assert report['review_required'] and report['blocking_suppressed_ranges']
    assert result['normalized_targets'][0]['guide_anchor_untrusted']
    assert result['generation_after_review_revision'] is None
    assert not example.calls and not example.cloud.enqueued


def test_validated_manual_identity_uses_same_gap_policy_after_matching_service_failure(example, monkeypatch):
    job = setup_clip(example, whole_gaps=True, confirmed=False)
    job.update(mask_identity_provenance='manual_source_review', tracking_reviewed=False,
        identity_matching_error='cloudflare_matching_unavailable',
        identity_matching_recheck_blocked=True, matching_fallback_used=True)
    example.records.data = deepcopy(job)
    fail_recheck_credentials(monkeypatch)
    result = example.pipeline.check_reviewed_mask_quality(job)
    assert not result['reviewed_mask_quality']['review_required']
    assert not result['reviewed_mask_quality']['blocking_suppressed_ranges']
    assert result['reviewed_mask_quality']['gap_tolerance']['assignments'][0]['identity_provenance'] == 'manual_source_review'
    assert not example.calls and not result.get('targeted_mask_review')
    # A source review never confers generation approval by this policy itself.
    assert result['generation_after_review_revision'] == job['generation_after_review_revision']


def test_existing_pending_v4_is_untouched_without_any_io(example, monkeypatch):
    job = setup_clip(example)
    job.update(tracking_plan={'version': 4}, targeted_mask_review={'pending': True})
    monkeypatch.setattr(example.cloud, 'download', lambda *args: pytest.fail('Pending source review must stay untouched'))
    assert example.pipeline.check_reviewed_mask_quality(job) == job
    assert not example.records.history and not example.calls


@pytest.fixture(autouse=True)
def legacy_continuity_regression_policy(monkeypatch):
    # Preserve explicit regression coverage for the paused implementation.
    # Production's default-disabled path is covered in test_mask_retention.py.
    monkeypatch.setattr('swapmein.mask_quality.CONTINUITY_CLEANUP_ENABLED', True)
