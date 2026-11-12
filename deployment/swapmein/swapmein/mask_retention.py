"""Retain assigned SAM pixels while geometric continuity cleanup is paused."""
import math
from pathlib import Path
import shutil

import numpy as np

from .errors import PublicError
from .mask_quality import _checked_inputs, _sha
from .tracking import FPS, _maps, _metadata

POLICY_VERSION = 'assigned_sam_masks_continuity_paused_v1'


def retain_assigned_masks(source, metadata, plan, choices, assignments, output, *, review_revision):
    source, output = Path(source), Path(output)
    width, height, frames, targets, labels, selected = _checked_inputs(
        source, metadata, plan, choices, assignments, output, review_revision)
    cuts = _metadata(metadata)[4]
    coverage, suppressed = [], []
    by_id = {target['id']: target for target in targets}
    with _maps(source, metadata) as read:
        for assignment in assignments:
            start, end = assignment['start_frame'], assignment['end_frame']
            if any(start < cut < end for cut in cuts):
                raise PublicError('Confirm each person separately across camera cuts.', code='tracking_invalid')
            if assignment.get('absent') or assignment.get('coordinate_fallback'):
                continue
            target_id = assignment.get('target_id', targets[0]['id'])
            anchor = (selected.get(assignment['segment_id']) or {}).get('anchor') or by_id[target_id]['anchor']
            frame = math.floor(anchor['time'] * FPS + 1e-7)
            x, y = min(width - 1, int(anchor['x'] * width)), min(height - 1, int(anchor['y'] * height))
            if read(frame)[y, x] != labels[target_id]:
                suppressed.append(dict(target_id=target_id, label=labels[target_id],
                    start_frame=start, end_frame=end, reason='review_anchor_has_no_component',
                    coordinate_guidance=True, absent=False))
        for frame in range(frames):
            coverage.append(int(np.count_nonzero(read(frame))))
    digest = _sha(source)
    shutil.copyfile(source, output)
    return dict(policy_version=POLICY_VERSION, review_revision=review_revision,
        input_sha256=digest, sha256=digest, coverage=coverage, frames=frames,
        continuity_cleanup_enabled=False, identity_verified=False,
        removed_pixels=0, changed_frames=0, component_removed_pixels=0,
        suppressed_pixels=0, suppressed_ranges=suppressed,
        review_required=bool(suppressed), no_identity_or_absence_inferred=True)
