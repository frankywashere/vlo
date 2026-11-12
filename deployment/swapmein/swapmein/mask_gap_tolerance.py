"""Forgive missing guide tint for confirmed identities without restoring pixels.

This policy changes whether a conservative omission needs another source check.
It never assigns identity, changes absence, or authorizes video generation.
"""
import math
from pathlib import Path

import cv2
import numpy as np

from .errors import PublicError
from .mask_quality import _checked_inputs, _sha
from .mask_recheck_plan import validate_supplemental
from .tracking import FPS, _maps, _metadata

POLICY_VERSION = 'confirmed_identity_gap_tolerance_v1'
MAX_GAP_FRAMES = 3 * FPS
MAX_CLIP_GAP_SHARE = .25
MIN_CLEAN_FRAMES = FPS // 2
MIN_RETAINED_SHARE = .80
MIN_MAIN_COMPONENT_RETAINED_SHARE = .95
MIN_ANCHOR_COMPONENT_RETAINED_SHARE = .99


def _ranges(indices):
    result = []
    for frame in sorted(indices):
        if result and result[-1][1] == frame:
            result[-1][1] += 1
        else:
            result.append([frame, frame + 1])
    return result


def _identity(job):
    provenance = job.get('mask_identity_provenance')
    if provenance == 'source_identity_match':
        return job.get('identity_matching_completed') is True, provenance
    if provenance not in (None, 'manual_source_review'):
        return False, 'unknown'
    if provenance is None and job.get('identity_matching_completed') is True:
        return True, 'source_identity_match'
    # Legacy manual records need the complete already-applied source review,
    # not merely a UI boolean or old no-review/fallback setting.
    manual = ((provenance == 'manual_source_review' or job.get('tracking_reviewed') is True)
              and bool(job.get('tracking_choices'))
              and bool(job.get('tracking_assignments'))
              and type(job.get('tracking_applied_revision')) is int
              and job['tracking_applied_revision'] >= 1)
    return manual, 'manual_source_review' if manual else 'unconfirmed'


def assess_mask_gaps(job, original_archive, checked_archive, candidates, *, review_revision):
    """Return a bounded, hash-bound omission assessment; read files only.

    Canonical version-1/2 source choices must already be validated. An existing
    pending version-4 component review remains explicit: deployment cannot
    silently accept choices the user is currently being asked to make.
    """
    source, checked = Path(original_archive), Path(checked_archive)
    assessment = dict(policy_version=POLICY_VERSION, input_sha256=_sha(source),
        output_sha256=_sha(checked), review_revision=review_revision,
        forgiven_candidate_ids=[], blocking_candidate_ids=[], tolerated_ranges=[],
        blocking_ranges=[], assignments=[], global_blockers=[], target_gap_totals=[])
    if not isinstance(candidates, list) or len(candidates) > 8:
        raise PublicError('Invalid saved mask checks.', code='tracking_invalid')
    candidate_ids = [item.get('candidate_id') for item in candidates if isinstance(item, dict)]
    if len(candidate_ids) != len(candidates) or any(not isinstance(value, str) for value in candidate_ids):
        raise PublicError('Invalid saved mask checks.', code='tracking_invalid')
    assessment['blocking_candidate_ids'] = candidate_ids.copy()
    if ((job.get('targeted_mask_review') or {}).get('pending')
            or job.get('tracking_plan', {}).get('version') == 4):
        assessment['global_blockers'] = ['pending_targeted_review']
        return assessment
    identity, provenance = _identity(job)
    for blocked, reason in (
        (not identity, 'identity_not_confirmed'),
        (job.get('matching_fallback_used') is True and provenance != 'manual_source_review', 'unreviewed_identity_fallback'),
        (job.get('tracking_resolution_pending') is True, 'source_review_unresolved'),
        (job.get('tracking_review_error') is True, 'source_review_error'),
        (job.get('recover_missing_masks') is True and not job.get('mask_recovery_completed'), 'mask_recovery_pending'),
        (type(review_revision) is not int or review_revision < 1
         or job.get('tracking_applied_revision') != review_revision
         or job.get('tracking_revision', review_revision) != review_revision, 'review_revision_mismatch')):
        if blocked:
            assessment['global_blockers'].append(reason)
    if assessment['global_blockers']:
        return assessment
    metadata, plan = job['tracking_metadata'], job['tracking_plan']
    if plan.get('version') not in (1, 2):
        assessment['global_blockers'] = ['source_mask_review_required']
        return assessment
    choices, assignments = job['tracking_choices'], job['tracking_assignments']
    # Reuse the sanitizer's exact section/choice/label/absence validation. It
    # reads only; checked is a distinct existing archive, never written here.
    width, height, frames, targets, labels, selected = _checked_inputs(
        source, metadata, plan, choices, assignments, checked, review_revision)
    _, _, _, _, cuts = _metadata(metadata)
    validated = validate_supplemental(source, metadata, assignments, candidates, review_revision=review_revision)
    if any(candidate['target_id'] not in labels or candidate['label'] != labels[candidate['target_id']]
           for candidate in validated):
        raise PublicError('Mask check labels must match their confirmed target.', code='tracking_invalid')
    targets_by_id = {item['id']: item for item in targets}
    normalized = {item['id']: item for item in job.get('normalized_targets', [])}
    frames_by_label = {label: [] for label in labels.values()}
    anchors = {}
    for assignment in assignments:
        target_id = assignment.get('target_id', targets[0]['id'])
        anchor = (selected.get(assignment['segment_id']) or {}).get('anchor') or targets_by_id[target_id].get('anchor')
        if not assignment.get('absent') and isinstance(anchor, dict):
            frame = math.floor(anchor['time'] * FPS + 1e-7)
            anchors.setdefault(frame, []).append((assignment['segment_id'], labels[target_id], anchor))
    anchor_checks = {}
    with _maps(source, metadata) as raw, _maps(checked, metadata) as kept:
        for frame in range(frames):
            before, after = raw(frame), kept(frame)
            if np.any((after != 0) & (after != before)):
                raise PublicError('The checked mask must only withhold original pixels.', code='tracking_invalid')
            components = {}
            for label in labels.values():
                region, retained = before == label, after == label
                count, component, stats, _ = cv2.connectedComponentsWithStats(region.astype(np.uint8), connectivity=8)
                components[label] = (component, stats)
                total, actual = int(region.sum()), int(retained.sum())
                largest = int(np.argmax(stats[1:, 4])) + 1 if count > 1 else 0
                main_pixels = int(stats[largest, 4]) if largest else 0
                main_kept = int(np.count_nonzero((component == largest) & retained)) if largest else 0
                share = actual / total if total else 0.
                main_share = main_kept / main_pixels if main_pixels else 0.
                frames_by_label[label].append(dict(raw_pixels=total, kept_pixels=actual,
                    retained_share=share, main_retained_share=main_share,
                    poor=(not actual or share < MIN_RETAINED_SHARE or main_share < MIN_MAIN_COMPONENT_RETAINED_SHARE)))
            for segment_id, label, anchor in anchors.get(frame, []):
                x, y = min(width - 1, int(anchor['x'] * width)), min(height - 1, int(anchor['y'] * height))
                component, stats = components[label]
                identifier = int(component[y, x])
                size = int(stats[identifier, 4]) if identifier else 0
                retained = int(np.count_nonzero((component == identifier) & (after == label))) if identifier else 0
                anchor_checks[segment_id] = dict(frame=frame, raw_pixels=size, retained_pixels=retained,
                    retained_share=retained / size if size else 0.,
                    point_retained=bool(before[y, x] == label and after[y, x] == label))
    poor_by_target = {target['id']: [] for target in targets}
    for assignment in assignments:
        if assignment.get('absent'):
            continue
        target_id = assignment.get('target_id', targets[0]['id'])
        label = labels[target_id]
        poor_by_target[target_id].extend(frame for frame in range(assignment['start_frame'], assignment['end_frame'])
                                        if frames_by_label[label][frame]['poor'])
    for target_id, indices in poor_by_target.items():
        assessment['target_gap_totals'].append(dict(target_id=target_id, poor_guide_frames=len(indices),
            clip_frames=frames, share=len(indices) / frames, within_limit=len(indices) <= frames * MAX_CLIP_GAP_SHARE))
    for assignment in assignments:
        target_id = assignment.get('target_id', targets[0]['id'])
        label = labels[target_id]
        start, end = assignment['start_frame'], assignment['end_frame']
        entry = dict(segment_id=assignment['segment_id'], target_id=target_id, label=label,
            start_frame=start, end_frame=end, identity_provenance=provenance,
            eligible=False, blocking_reasons=[], absent=assignment.get('absent') is True)
        assessment['assignments'].append(entry)
        if assignment.get('absent'):
            continue
        target = normalized.get(target_id, targets_by_id[target_id])
        reasons = entry['blocking_reasons']
        if target.get('guide_anchor_untrusted') or any(scene.get('guide_anchor_untrusted')
                for scene in target.get('scenes', []) if scene.get('start', 0) * FPS < end
                and scene.get('end', frames / FPS) * FPS > start):
            reasons.append('identity_anchor_untrusted')
        if assignment.get('coordinate_fallback'):
            reasons.append('coordinate_fallback')
        if any(start < cut < end for cut in cuts):
            reasons.append('assignment_crosses_cut')
        anchor = anchor_checks.get(assignment['segment_id']) or {}
        entry['anchor'] = anchor
        if (not anchor.get('point_retained') or anchor.get('retained_share', 0) < MIN_ANCHOR_COMPONENT_RETAINED_SHARE
                or frames_by_label[label][anchor.get('frame', start)]['poor']):
            reasons.append('source_anchor_not_retained')
        rows = frames_by_label[label][start:end]
        seed_pixels = anchor.get('raw_pixels', 0)
        clean_frames = sum(not row['poor'] and row['kept_pixels'] >= max(1, seed_pixels * .25) for row in rows)
        poor = [start + index for index, row in enumerate(rows) if row['poor']]
        gaps = _ranges(poor)
        missing = _ranges(start + index for index, row in enumerate(rows) if not row['kept_pixels'])
        entry.update(clean_frames=clean_frames, poor_guide_frames=len(poor), poor_guide_ranges=gaps,
                     whole_mask_missing_ranges=missing,
                     maximum_gap_frames=max((b - a for a, b in gaps), default=0))
        if clean_frames < MIN_CLEAN_FRAMES:
            reasons.append('insufficient_same_shot_guide')
        if any(b - a > MAX_GAP_FRAMES for a, b in gaps):
            reasons.append('gap_too_long')
        if len(poor_by_target[target_id]) > frames * MAX_CLIP_GAP_SHARE:
            reasons.append('clip_gap_budget_exceeded')
        entry['eligible'] = not reasons
        destination = assessment['tolerated_ranges'] if entry['eligible'] else assessment['blocking_ranges']
        for a, b in gaps:
            destination.append(dict(target_id=target_id, label=label, segment_id=assignment['segment_id'],
                start_frame=a, end_frame=b, kind='brief_guide_gap',
                reason='confirmed_identity_video_inference' if entry['eligible'] else ','.join(reasons)))
        minor = [start + index for index, row in enumerate(rows)
                 if row['raw_pixels'] > row['kept_pixels'] and not row['poor']]
        for a, b in _ranges(minor):
            destination.append(dict(target_id=target_id, label=label, segment_id=assignment['segment_id'],
                start_frame=a, end_frame=b, kind='minor_untinted_component',
                reason='confirmed_main_component_retained' if entry['eligible'] else ','.join(reasons)))
    entries = {entry['segment_id']: entry for entry in assessment['assignments']}
    assessment['forgiven_candidate_ids'] = [candidate['candidate_id'] for candidate in validated
                                           if entries[candidate['segment_id']]['eligible']]
    forgiven = set(assessment['forgiven_candidate_ids'])
    assessment['blocking_candidate_ids'] = [identifier for identifier in candidate_ids if identifier not in forgiven]
    assessment['thresholds'] = dict(max_gap_frames=MAX_GAP_FRAMES, max_clip_gap_share=MAX_CLIP_GAP_SHARE,
        minimum_clean_frames=MIN_CLEAN_FRAMES, minimum_retained_share=MIN_RETAINED_SHARE,
        minimum_main_component_retained_share=MIN_MAIN_COMPONENT_RETAINED_SHARE,
        minimum_anchor_component_retained_share=MIN_ANCHOR_COMPONENT_RETAINED_SHARE)
    return assessment
