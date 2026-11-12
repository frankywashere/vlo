"""Withhold only highly fragmented, one-frame SAM residue at verified cuts.

This is a geometry/temporal exclusion, never an identity assignment. It applies
before matching to selected-person Meta masks; coherent, brief and uncertain
people still go through ordinary matching/review. Keep the raw archive.
"""
from io import BytesIO
import hashlib
from pathlib import Path
import shutil
import zipfile

import cv2
import numpy as np
from PIL import Image

from .errors import PublicError
from .tracking import FPS, _maps, _metadata

POLICY_VERSION = 'meta_cut_residue_v1'
MIN_COMPONENTS = 32
MAX_FRAME_SHARE = .01
MAX_COMPONENT_SHARE = .40
MAX_LARGEST_BOX_FILL = .25
MAX_INTERIOR_RADIUS_SHARE = .0125
MAX_PREVIOUS_AREA_SHARE = .10
MIN_PRIOR_DOMINANT_SHARE = .80
MIN_FOLLOWING_ABSENT = 3


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _geometry(region):
    total = int(np.count_nonzero(region))
    if not total:
        return None
    count, _, stats, _ = cv2.connectedComponentsWithStats(region.astype(np.uint8), connectivity=8)
    components = stats[1:]
    largest = components[int(np.argmax(components[:, cv2.CC_STAT_AREA]))]
    area = int(largest[cv2.CC_STAT_AREA])
    box_area = int(largest[cv2.CC_STAT_WIDTH]) * int(largest[cv2.CC_STAT_HEIGHT])
    radius = float(cv2.distanceTransform(region.astype(np.uint8), cv2.DIST_L2, 5).max())
    return dict(pixels=total, components=count - 1, largest_pixels=area,
                largest_share=area / total, largest_box_fill=area / box_area,
                maximum_interior_radius=radius)


def _anchor_frames(metadata, targets):
    anchors = [metadata['anchor'], *[target['anchor'] for target in targets]]
    for target in targets:
        anchors.extend(scene['anchor'] for scene in target.get('scenes', [])
                       if not scene.get('absent') and isinstance(scene.get('anchor'), dict))
    return {_metadata({**metadata, 'anchor': anchor})[3] for anchor in anchors}


def sanitize_cut_boundary_masks(archive, output, metadata, *, validated_cut_frames=(), targets=()):
    """Write a separate archive and report exactly which existing pixels lose tint.

    `validated_cut_frames` must come from the server's RGB cut detector. A cut
    merely present in unvalidated metadata is insufficient. All future frames
    in the same shot must contain zero pixels for the candidate label; a brief
    occlusion or re-entry therefore cannot satisfy this policy.
    """
    archive, output = Path(archive), Path(output)
    width, height, frames, _, cuts = _metadata(metadata)
    if (archive.resolve() == output.resolve() or output.exists() or output.is_symlink()
            or not isinstance(validated_cut_frames, (list, tuple, set, frozenset))
            or any(type(frame) is not int or frame not in cuts for frame in validated_cut_frames)):
        raise PublicError('Preserve the raw masks and validate cut boundaries before filtering.', code='tracking_invalid')
    validated = set(validated_cut_frames)
    protected = _anchor_frames(metadata, targets)
    changes, observations = {}, []
    report = dict(policy_version=POLICY_VERSION, provider_scope='meta_selected_people',
                  source_sha256=_sha(archive), frames=frames, removed=[], removed_pixels=0)
    try:
        with _maps(archive, metadata) as read:
            # Limit exclusions to IDs selected by the original source taps.
            selected = set()
            for target in targets or [{'anchor': metadata['anchor']}]:
                anchor = target['anchor']
                frame = _metadata({**metadata, 'anchor': anchor})[3]
                x, y = min(width - 1, int(anchor['x'] * width)), min(height - 1, int(anchor['y'] * height))
                label = int(read(frame)[y, x])
                if label:
                    selected.add(label)
            boundaries = [0, *cuts, frames]
            for index, cut in enumerate(cuts):
                start, end = boundaries[index], boundaries[index + 2]
                if cut not in validated or cut in protected or cut - start < 2 or end - cut <= MIN_FOLLOWING_ABSENT:
                    continue
                current = read(cut)
                possible = []
                for label in sorted(set(int(value) for value in np.unique(current) if value) & selected):
                    region = current == label
                    total = int(np.count_nonzero(region))
                    if total > width * height * MAX_FRAME_SHARE:
                        continue
                    geometry = _geometry(region)
                    if (geometry['components'] < MIN_COMPONENTS
                            or geometry['largest_share'] > MAX_COMPONENT_SHARE
                            or geometry['largest_box_fill'] > MAX_LARGEST_BOX_FILL
                            or geometry['maximum_interior_radius'] > min(width, height) * MAX_INTERIOR_RADIUS_SHARE):
                        continue
                    prior = [_geometry(read(frame) == label) for frame in (cut - 2, cut - 1)]
                    if any(item is None or item['largest_share'] < MIN_PRIOR_DOMINANT_SHARE
                           or total > item['pixels'] * MAX_PREVIOUS_AREA_SHARE for item in prior):
                        continue
                    possible.append((label, geometry, prior))
                if not possible:
                    continue
                # Any later sighting in this shot keeps the candidate untouched.
                future = set()
                for frame in range(cut + 1, end):
                    future.update(int(value) for value in np.unique(read(frame)) if value)
                for label, geometry, prior in possible:
                    if label in future:
                        continue
                    changes.setdefault(cut, set()).add(label)
                    observations.append(dict(frame=cut, label=label, **geometry,
                        prior_pixels=[item['pixels'] for item in prior],
                        following_absent_frames=end - cut - 1, reason='fragmented_cut_residue'))
            # Validate every image, including frames not involved in a candidate.
            # Preserve PNG bytes everywhere except the exact excluded cut frame.
            with zipfile.ZipFile(archive) as source, zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as destination:
                for frame in range(frames):
                    labels = read(frame)
                    name = f'labels/labels_{frame:05d}.png'
                    if frame not in changes:
                        destination.writestr(name, source.read(name))
                        continue
                    filtered = labels.copy()
                    for label in changes[frame]:
                        filtered[labels == label] = 0
                    encoded = BytesIO()
                    Image.fromarray(filtered.astype(np.uint16)).save(encoded, 'PNG')
                    destination.writestr(name, encoded.getvalue())
                for info in source.infolist():
                    if '/' not in info.filename and info.filename.endswith('.json'):
                        destination.writestr(info.filename, source.read(info.filename))
        if not observations:
            shutil.copyfile(archive, output)
        report.update(removed=observations, removed_pixels=sum(item['pixels'] for item in observations),
                      output_sha256=_sha(output))
        return report
    except Exception:
        output.unlink(missing_ok=True)
        raise
