"""Explicit source-coordinate recovery after uncertain guide masks.

A geometric mask rejection is not an identity classifier. Re-review only the
untrusted identities on original pixels, after withholding all of their tint.
"""
from copy import deepcopy
from pathlib import Path
import zipfile

import numpy as np

from .errors import PublicError
from .mask_quality import _png, _sha, _write
from .scene_selection import analyze, mask_quality_targets, resolve
from .tracking import _maps, _metadata


def _unsafe_ids(job):
    return [target['id'] for target in job.get('normalized_targets', [])
            if target.get('guide_anchor_untrusted')]


def withhold_untrusted_masks(source: Path, output: Path, job: dict) -> dict:
    """Remove complete unsafe identity labels, preserving other label pixels."""
    source, output = Path(source), Path(output)
    temporary = output.with_name(output.name + '.partial')
    if output.exists() or output.is_symlink() or temporary.exists() or temporary.is_symlink() or source.resolve() == output.resolve():
        raise PublicError('Preserve existing guide masks during identity recovery.', code='tracking_invalid')
    unsafe = set(_unsafe_ids(job))
    if not unsafe:
        raise PublicError('No identity needs source-coordinate recovery.', code='tracking_invalid')
    metadata = job['tracking_metadata']
    width, height, frames, _, _ = _metadata(metadata)
    originals = job.get('tracking_plan', {}).get('targets') or metadata.get('targets') or job['normalized_targets']
    labels = {target['id']: index for index, target in enumerate(originals, 1)}
    if not unsafe <= labels.keys():
        raise PublicError('The recovery identities do not match the saved masks.', code='tracking_invalid')
    removed_labels = [labels[identifier] for identifier in unsafe]
    report = deepcopy(job.get('reviewed_mask_quality') or {})
    prior_sha = report.get('sha256')
    if prior_sha and _sha(source) != prior_sha:
        raise PublicError('The guide masks changed before identity recovery.', code='tracking_invalid')
    report['identity_recovery_input_sha256'] = _sha(source)
    coverage, removed, changed = [], 0, 0
    try:
        with _maps(source, metadata) as read, zipfile.ZipFile(temporary, 'w') as archive:
            for frame in range(frames):
                original = read(frame)
                selected = np.isin(original, removed_labels)
                mask = original.copy()
                mask[selected] = 0
                removed += int(selected.sum())
                changed += bool(selected.any())
                coverage.append(int((mask > 0).sum()))
                _write(archive, f'labels/labels_{frame:05d}.png', _png(mask))
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    report['suppressed_ranges'] = [item for item in report.get('suppressed_ranges', []) if item.get('target_id') not in unsafe]
    report['suppressed_ranges'].extend({'target_id': identifier, 'label': labels[identifier],
        'start_frame': 0, 'end_frame': frames, 'reason': 'identity_reselection',
        'coordinate_guidance': True, 'absent': False} for identifier in sorted(unsafe))
    report.update(sha256=_sha(output), coverage=coverage, identity_recovery_target_ids=sorted(unsafe),
                  identity_recovery_removed_pixels=removed, identity_recovery_changed_frames=changed,
                  review_required=True, frames=frames, no_identity_or_absence_inferred=True)
    report['removed_pixels'] = report.get('removed_pixels', 0) + removed
    return report


def identity_recovery_fields(job: dict) -> dict | None:
    """Reopen a canonical v3 review for only unsafe targets, never all targets."""
    unsafe = set(_unsafe_ids(job))
    if not unsafe:
        return None
    revision = job.get('tracking_applied_revision', job.get('tracking_revision', 0)) + 1
    if not 1 <= revision <= 100:
        raise PublicError('This clip needs a new preparation before further review.', 409, code='tracking_review_invalid')
    selected = []
    for index, target in enumerate(job['normalized_targets']):
        if target['id'] not in unsafe:
            continue
        item = {key: deepcopy(value) for key, value in target.items()
                if key not in {'scenes', 'guide_anchor_untrusted', 'guide_mask_suppressed_ranges'}}
        item.setdefault('name', f'Person {index + 1}')
        selected.append(item)
    plan = analyze(job['tracking_metadata'], selected)
    return dict(status='tracking_review', tracking_plan=plan, tracking_revision=revision,
        tracking_reviewed=False, tracking_choices=[], tracking_assignments=[],
        identity_recovery=True, identity_recovery_target_ids=[target['id'] for target in selected],
        tracking_review_error=False, tracking_error_segment_id=None,
        tracking_resolution_pending=False, generation_after_review_revision=None,
        review_generation_queued=False, capacity_generation_authorized=False,
        capacity_approval_revision=None, generation_capacity_token=None,
        mask_quality_approved_sha256=None, mask_quality_approved_revision=None,
        preparation_kind='initial', review_required=True,
        message='Some person masks were uncertain. Select these people on the original video in each scene, or mark them not visible. Their tint has been withheld. No generation has been submitted.')


def resolve_identity_recovery(job: dict, choices: list) -> list:
    """Merge explicit source selections without sending them through SAM again."""
    unsafe = job.get('identity_recovery_target_ids')
    plan = job.get('tracking_plan') or {}
    if not job.get('identity_recovery') or not unsafe or {t['id'] for t in plan.get('targets', [])} != set(unsafe):
        raise PublicError('Refresh the identity review before continuing.', 409, code='tracking_review_invalid')
    try:
        recovered = resolve(job['tracking_metadata'], plan, choices)
    except PublicError as error:
        # resolve validates canonical plan, every choice and all coordinates
        # before its final all-absent rule. Other targets may still be visible.
        if error.message != 'Select at least one person to replace.' or not all(c.get('absent') is True for c in choices):
            raise
        sections = {section['id']: section for section in plan['segments']}
        recovered = [{**target, 'scenes': [dict(start=sections[choice['segment_id']]['start'],
            end=sections[choice['segment_id']]['end'], absent=True, anchor=None)
            for choice in choices if sections[choice['segment_id']]['target_id'] == target['id']]}
            for target in plan['targets']]
    recovered = mask_quality_targets(recovered, job.get('reviewed_mask_quality') or {})
    by_id = {}
    for target in recovered:
        target.pop('guide_anchor_untrusted', None)
        visible = [scene for scene in target['scenes'] if not scene['absent']]
        for scene in target['scenes']:
            scene.pop('guide_anchor_untrusted', None)
            if not scene['absent']:
                scene['coordinate_fallback'] = True
        if visible:
            target['anchor'] = deepcopy(min(visible, key=lambda scene: scene['start'])['anchor'])
        target['coordinate_fallback'] = True
        by_id[target['id']] = target
    merged = [by_id.get(target['id'], deepcopy(target)) for target in job['normalized_targets']]
    if not any(not scene.get('absent') for target in merged for scene in target.get('scenes', []) or [{}]):
        raise PublicError('Select at least one person to replace.', code='tracking_review_invalid')
    return merged
