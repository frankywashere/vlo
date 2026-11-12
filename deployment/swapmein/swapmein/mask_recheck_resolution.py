"""Apply explicit supplemental source points without losing prior mask evidence."""
from copy import deepcopy
import math
from pathlib import Path
import tempfile

from .errors import PublicError
from .mask_quality import _components, _sha
from .mask_recheck_plan import component_at, component_hash
from .mask_recheck_runtime import context_key
from .targeted_mask_review import resolve_targeted_review
from .tracking import FPS, _maps


def resolve_targeted_masks(pipeline, job):
    snapshot = job['targeted_mask_review']
    # The authenticated POST claimed this exact canonical review before the
    # background worker began. Validate its frozen parent state again.
    review_job = {**job, 'status': 'tracking_review',
        'tracking_applied_revision': snapshot['base_tracking_applied_revision']}
    decisions = resolve_targeted_review(review_job, job['tracking_choices'])
    revision = job['tracking_applied_revision']
    input_uri = job['mask_recheck_input_uri']
    candidates = {item['candidate_id']: item for item in snapshot['checks']}
    supplements = {item['candidate_id']: deepcopy(item) for item in job.get('mask_supplemental_anchors', [])}
    with tempfile.TemporaryDirectory() as temporary:
        original = Path(temporary) / 'reviewed.zip'
        pipeline.cloud.download(input_uri, original, 80 * 1024 * 1024)
        if _sha(original) != snapshot['base_masks_sha256']:
            raise PublicError('The original mask evidence changed. Refresh this clip.', 409, code='tracking_review_invalid')
        with _maps(original, snapshot['base_tracking_metadata']) as read:
            for decision in decisions:
                if decision['absent']:
                    continue  # Dismiss this fragment only; baseline stays intact.
                candidate = candidates[decision['candidate_id']]
                frame = math.floor(decision['anchor']['time'] * FPS + 1e-7)
                labels = read(frame)
                try:
                    component, stats = component_at(labels, candidate['label'], decision['anchor'])
                    if frame != candidate['frame'] or component_hash(component) != candidate['component_sha256']:
                        raise PublicError('Tap the highlighted part, or skip this fragment.', code='tracking_review_invalid')
                except PublicError as error:
                    error.segment_id = next(s['id'] for s in job['tracking_plan']['segments']
                        if s['candidate_id'] == decision['candidate_id'])
                    raise
                h, w = labels.shape
                x = min(w - 1, int(decision['anchor']['x'] * w))
                y = min(h - 1, int(decision['anchor']['y'] * h))
                component_id = int(_components(labels == candidate['label'])[1][y, x])
                bx, by, bw, bh = map(int, stats[:4])
                supplements[candidate['candidate_id']] = {**candidate, 'anchor': decision['anchor'],
                    'frame': frame, 'component_id': component_id, 'box': [bx, by, bx + bw, by + bh],
                    'component_sha256': component_hash(component), 'review_revision': revision,
                    'confirmation': 'explicit_source_review'}
    restored = {field: deepcopy(snapshot['base_' + field]) for field in (
        'tracking_plan', 'tracking_choices', 'tracking_assignments', 'tracking_metadata', 'normalized_targets')}
    parent = {**job, **restored}
    for item in supplements.values():
        item['review_revision'] = revision
    return pipeline.update_job(job['id'], **restored,
        reviewed_masks_uri=input_uri, tracking_resolution_pending=False,
        mask_supplemental_anchors=list(supplements.values()),
        mask_supplemental_context_key=context_key(parent, snapshot['base_masks_sha256']),
        targeted_mask_review={**snapshot, 'pending': False, 'decisions': decisions,
            'completed_review_key': snapshot['review_key']},
        mask_quality_approved_sha256=None, mask_quality_approved_revision=None,
        generation_after_review_revision=None, review_generation_queued=False,
        capacity_generation_authorized=False, capacity_approval_revision=None,
        generation_capacity_token=None)
