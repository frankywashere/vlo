"""Bounded proposals for withheld components; geometry never confirms identity."""
from collections import defaultdict
import hashlib
import json
import math

import cv2
import numpy as np

from .errors import PublicError
from .mask_quality import _components, _sha
from .tracking import FPS, _maps, _metadata

MAX_RECHECKS = 8
MIN_COMPONENT_PIXELS = 64
MAX_NODES = 8192


def component_hash(mask):
    return hashlib.sha256(np.ascontiguousarray(mask, dtype=np.uint8).tobytes()).hexdigest()


def component_at(labels, label, anchor):
    h, w = labels.shape
    n, components, stats, _ = _components(labels == label)
    x, y = min(w - 1, int(anchor['x'] * w)), min(h - 1, int(anchor['y'] * h))
    index = int(components[y, x])
    if not index:
        raise PublicError('Select the visible person in this frame.', code='tracking_review_invalid')
    return components == index, stats[index]


def _box(stats):
    x, y, w, h = map(int, stats[:4])
    return [x, y, x + w, y + h]


def plan_rechecks(archive, checked, metadata, plan, choices, assignments, *, review_revision):
    """Group missing adjacent-frame components, then propose one view per branch.

    Graphs only reduce duplicate questions. No component is kept by this graph.
    Each proposal still requires affirmative source identity evidence. Separate
    assignments never share a graph, including at real camera cuts.
    """
    width, height, _, _, cuts = _metadata(metadata)
    targets = plan.get('targets') or metadata.get('targets') or [{'id': 'selected', 'anchor': metadata['anchor']}]
    selected = {c['segment_id']: c for c in choices}
    labels = {t['id']: i for i, t in enumerate(targets, 1)}
    input_sha = _sha(archive)
    proposals = []
    with _maps(archive, metadata) as raw, _maps(checked, metadata) as clean:
        for assignment in assignments:
            if assignment.get('absent') or assignment.get('coordinate_fallback'):
                continue
            if any(assignment['start_frame'] < cut < assignment['end_frame'] for cut in cuts):
                continue  # A scene boundary needs independent reviewed evidence.
            target_id = assignment.get('target_id', targets[0]['id'])
            label = labels[target_id]
            anchor = selected.get(assignment['segment_id'], {}).get('anchor') or next(t['anchor'] for t in targets if t['id'] == target_id)
            seed_frame = math.floor(anchor['time'] * FPS + 1e-7)
            seed = raw(seed_frame)
            try:
                reference_mask, reference_stats = component_at(seed, label, anchor)
            except PublicError:
                continue
            # Never use an already rejected seed as new identity evidence.
            if not np.any(reference_mask & (clean(seed_frame) == label)):
                continue
            parents, nodes, previous, previous_ids = [], [], None, {}
            def find(i):
                while parents[i] != i:
                    parents[i] = parents[parents[i]]
                    i = parents[i]
                return i
            def union(a, b):
                a, b = find(a), find(b)
                if a != b:
                    parents[b] = a
            for frame in range(assignment['start_frame'], assignment['end_frame']):
                source, kept = raw(frame), clean(frame)
                n, components, stats, _ = _components(source == label)
                touched = set(map(int, np.unique(components[kept == label])))
                ids = {}
                for k in range(1, n):
                    if k in touched or stats[k, 4] < MIN_COMPONENT_PIXELS:
                        continue
                    if len(nodes) >= MAX_NODES:
                        break  # Remaining tint stays withheld, never auto-approved.
                    index = len(nodes)
                    parents.append(index)
                    nodes.append(dict(frame=frame, component_id=k, area=int(stats[k, 4]), box=_box(stats[k])))
                    ids[k] = index
                if previous is not None and ids and previous_ids:
                    both = np.isin(previous, list(previous_ids)) & np.isin(components, list(ids))
                    if both.any():
                        for a, b in np.unique(np.stack([previous[both], components[both]], axis=1), axis=0):
                            union(previous_ids[int(a)], ids[int(b)])
                previous, previous_ids = components, ids
            groups = defaultdict(list)
            for index, node in enumerate(nodes):
                groups[find(index)].append(node)
            for group in groups.values():
                best = max(group, key=lambda item: (item['area'], -item['frame']))
                mask = _components(raw(best['frame']) == label)[1] == best['component_id']
                distance = cv2.distanceTransform(mask.astype(np.uint8), cv2.DIST_L2, 3)
                y, x = np.unravel_index(int(distance.argmax()), mask.shape)
                candidate = dict(target_id=target_id, label=label, segment_id=assignment['segment_id'],
                    start_frame=assignment['start_frame'], end_frame=assignment['end_frame'],
                    frame=best['frame'], component_id=best['component_id'], component_sha256=component_hash(mask),
                    box=best['box'], anchor={'x': (int(x) + .5) / width, 'y': (int(y) + .5) / height, 'time': best['frame'] / FPS},
                    reference_anchor=anchor, reference_box=_box(reference_stats), input_sha256=input_sha,
                    review_revision=review_revision, first_frame=min(v['frame'] for v in group),
                    last_frame=max(v['frame'] for v in group), pixels=best['area'])
                binding = json.dumps(candidate, sort_keys=True, separators=(',', ':'), allow_nan=False)
                candidate['candidate_id'] = hashlib.sha256(binding.encode()).hexdigest()[:32]
                proposals.append(candidate)
    return sorted(proposals, key=lambda item: (-item['pixels'], item['target_id'], item['frame']))[:MAX_RECHECKS]


def validate_supplemental(archive, metadata, assignments, proposals, *, review_revision):
    """Rebind saved affirmative/manual evidence to exact immutable mask pixels."""
    if not isinstance(proposals, list) or len(proposals) > MAX_RECHECKS:
        raise PublicError('Refresh the mask identity checks.', code='tracking_invalid')
    assigned = {a['segment_id']: a for a in assignments}
    _, _, _, _, cuts = _metadata(metadata)
    checked, seen, digest = [], set(), _sha(archive)
    with _maps(archive, metadata) as read:
        for item in proposals:
            try:
                a = assigned[item['segment_id']]
                frame = item['frame']
                anchor = item['anchor']
                if (item['candidate_id'] in seen or item['input_sha256'] != digest
                        or item['review_revision'] != review_revision or type(frame) is not int
                        or not a['start_frame'] <= frame < a['end_frame']
                        or item['start_frame'] != a['start_frame'] or item['end_frame'] != a['end_frame']
                        or any(a['start_frame'] < cut < a['end_frame'] for cut in cuts)
                        or item['target_id'] != a.get('target_id', 'selected')
                        or a.get('absent') or a.get('coordinate_fallback')
                        or type(item['label']) is not int or item['label'] < 1
                        or any(type(anchor[k]) not in (int, float) or not math.isfinite(anchor[k]) for k in ['x','y','time'])
                        or not 0 <= anchor['x'] <= 1 or not 0 <= anchor['y'] <= 1
                        or math.floor(anchor['time'] * FPS + 1e-7) != frame):
                    raise ValueError()
                mask, _ = component_at(read(frame), item['label'], anchor)
                if component_hash(mask) != item['component_sha256']:
                    raise ValueError()
                seen.add(item['candidate_id'])
                checked.append(item)
            except (KeyError, TypeError, ValueError, OverflowError, PublicError):
                raise PublicError('The saved mask identity evidence changed. Review this selection again.', code='tracking_invalid') from None
    return checked
