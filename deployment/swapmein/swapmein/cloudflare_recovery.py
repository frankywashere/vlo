"""Recover initial taps without discarding the other raw SAM person tracks."""
from io import BytesIO
from pathlib import Path
import uuid
import zipfile

import numpy as np
from PIL import Image

from .diagnostics import emit, timed
from .errors import PublicError
from .mask_recovery import run_mask_recovery
from .multi_tracking import selected_tracks
from .tracking import _maps, _metadata, _pixel_label


def _failure():
    return PublicError("A missing initial person mask could not be recovered safely. Check scene matches.",
                       409, code="cloudflare_anchor_recovery")


def _component_evidence(region, source, point=None):
    """Measure one connected recovery body, including its original SAM pixels.

    Row runs avoid allocating a Python object for every foreground pixel. The
    anchor must belong to the main component, not a disconnected SAM island.
    """
    parents, sizes, overlaps = [], [], []
    previous, anchor = [], None

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for y, row in enumerate(region):
        changes = np.flatnonzero(np.diff(np.r_[False, row, False]))
        current, cursor = [], 0
        for left, right in zip(changes[::2], changes[1::2]):
            left, right = int(left), int(right)
            index = len(parents)
            parents.append(index)
            sizes.append(right - left)
            overlaps.append(int(np.count_nonzero(source[y, left:right])))
            if point is not None and y == point[1] and left <= point[0] < right:
                anchor = index
            while cursor < len(previous) and previous[cursor][1] < left:
                cursor += 1
            for old_left, old_right, old_index in previous[cursor:]:
                if old_left > right:
                    break
                a, b = find(index), find(old_index)
                if a != b:
                    if sizes[a] < sizes[b]:
                        a, b = b, a
                    parents[b] = a
                    sizes[a] += sizes[b]
                    overlaps[a] += overlaps[b]
            current.append((left, right, index))
        previous = current
    roots = [index for index in range(len(parents)) if parents[index] == index]
    if not roots:
        return 0, 0, False
    largest = max(roots, key=lambda index: sizes[index])
    return sizes[largest], overlaps[largest], point is None or (anchor is not None and find(anchor) == largest)


def _contained_overlap_labels(original, repaired, metadata, requests, tracks):
    """Allow only a single-person-shot extension of an existing partial mask.

    A recovery rooted in the user's tap may include the person's existing
    jacket mask. That is not a collision with a second person. Containment
    alone is insufficient in a multi-person shot, so those cases still stop
    for review. This does not establish identities across cuts; the normal
    source-image matching step remains responsible for that.
    """
    width, height, _, _, _ = _metadata(metadata)
    replacements, report = {}, []
    anchor_frames = [_metadata({**metadata, "anchor": target["anchor"]})[3] for target in tracks]
    for request in requests:
        scene_ids, overlap_ids, observations = set(), set(), []
        source_frames, recovered_frames = 0, 0
        start, end = request["start_frame"], request["end_frame"]
        for frame in range(start, end):
            source = original(frame)
            region = repaired(frame) == request["label"]
            source_region = source != 0
            source_pixels, recovered_pixels = int(np.count_nonzero(source_region)), int(np.count_nonzero(region))
            scene_ids.update(int(label) for label in np.unique(source) if label)
            source_frames += bool(source_pixels)
            recovered_frames += bool(recovered_pixels)
            overlap_ids.update(int(label) for label in np.unique(source[region]) if label)
            if recovered_pixels:
                point = ((min(width - 1, int(request["x"] * width)),
                          min(height - 1, int(request["y"] * height))) if frame == request["frame"] else None)
                main_pixels, main_overlap, anchor_in_main = _component_evidence(region, source_region, point)
                observations.append((source_pixels, recovered_pixels, main_pixels, main_overlap, anchor_in_main))
        if not overlap_ids:
            continue
        # Never join two raw tracks, another selected anchor, or a one-frame
        # coincidence. Requiring the whole scene to have one ID deliberately
        # leaves crowded/partially detected shots for the existing review.
        if (len(overlap_ids) != 1 or scene_ids != overlap_ids or source_frames != recovered_frames
                or recovered_frames < 3
                or sum(start <= frame < end for frame in anchor_frames) != 1
                or any(not source_pixels or main_overlap / source_pixels < .99
                       or main_overlap / recovered_pixels < .20
                       or main_pixels / recovered_pixels < .999 or not anchor_in_main
                       for source_pixels, recovered_pixels, main_pixels, main_overlap, anchor_in_main in observations)):
            raise _failure()
        label = next(iter(overlap_ids))
        replacements[request["label"]] = label
        report.append({"segment_id": request["segment_id"], "policy": "single_person_containment_v1",
                       "source_label": label, "evidence_frames": recovered_frames,
                       "minimum_original_containment": min(item[3] / item[0] for item in observations),
                       "minimum_recovery_overlap": min(item[3] / item[1] for item in observations),
                       "minimum_main_component_share": min(item[2] / item[1] for item in observations)})
    return replacements, report


@timed("identity_anchor_recovery", job_arg="job")
def recover_identity_anchors(pipeline, job, masks, metadata, targets, root, remaining):
    """Return a separate repaired raw archive, or None when no retry is needed.

    The existing point-recovery worker accepts only selected labels 1..5. Give
    it an empty archive with those temporary slots, then merge its additions
    into the original all-person SAM archive under globally unused labels. A
    tightly bounded single-person containment case extends its original ID.
    """
    permissive = {**metadata, "allow_unmatched_anchor": True, "review_all_scenes": True}
    tracks = selected_tracks(masks, permissive, targets, allow_unmatched=True)
    missing = [target for target in tracks if target["selected_label"] == 0]
    if not missing or job.get("retry_missing_masks_with_sam") is not True:
        return None
    if job.get("sam_provider", job.get("preparation_provider", "workers")) == "meta":
        # The approved initial SAM budget already includes the full-person
        # request. An automatic second recovery batch would spend beyond it.
        # This distinct code intentionally bypasses the no-review fallback and
        # uses the pipeline's existing explicit scene-selection review instead.
        emit("meta_initial_anchor_review_required", job_id=job["id"], missing_targets=len(missing),
             additional_paid_requests=0)
        raise PublicError("A selected person's mask needs scene review. Check the saved selections before continuing; no additional mask request or generation was submitted.",
                          409, code="meta_initial_anchor_review")
    width, height, frames, _, cuts = _metadata(metadata)
    used = set()
    with _maps(masks, metadata) as read:
        for frame in range(frames):
            used.update(int(label) for label in np.unique(read(frame)))
    unused = (label for label in range(1, 65536) if label not in used)
    requests, raw_labels = [], {}
    boundaries = [0, *cuts, frames]
    for slot, target in enumerate(missing, 1):
        anchor = target["anchor"]
        frame = _metadata({**metadata, "anchor": anchor})[3]
        start, end = next((start, end) for start, end in zip(boundaries, boundaries[1:]) if start <= frame < end)
        label = next(unused, None)
        if label is None:
            raise _failure()
        raw_labels[slot] = label
        requests.append({"segment_id": "identity-initial-" + target["id"], "start_frame": start,
            "end_frame": end, "frame": frame, "x": anchor["x"], "y": anchor["y"], "label": slot})
    root = Path(root)
    empty = root / ("identity-recovery-empty-" + uuid.uuid4().hex + ".zip")
    buffer = BytesIO()
    Image.fromarray(np.zeros((height, width), dtype=np.uint16)).save(buffer, "PNG")
    with zipfile.ZipFile(empty, "w", zipfile.ZIP_DEFLATED) as archive:
        for frame in range(frames):
            archive.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
    prefix = f"inputs/swapmein-{job['id']}/identity-recovery-{uuid.uuid4().hex}/"
    empty_uri = pipeline.cloud.upload(pipeline.settings.worker_bucket, prefix + "empty-masks.zip", empty, "application/zip")
    saved, _ = pipeline.records.read(f"jobs/{job['id']}.json")
    recovery_job = {**job, **(saved or {}), "reviewed_masks_uri": empty_uri,
                    "tracking_metadata": metadata, "recovery_entry_point": "automatic_identity_anchor"}
    pipeline.update_job(job['id'], identity_anchor_recovery_attempted=True)
    recovered_uri, _, report = run_mask_recovery(pipeline, recovery_job, requests, remaining)
    pipeline.update_job(job['id'], identity_anchor_recovery_report=report)
    if (len(report) != len(requests) or {item.get("segment_id") for item in report}
            != {item["segment_id"] for item in requests}
            or any(item.get("outcome") != "recovered" for item in report)):
        raise _failure()
    recovered = root / ("identity-recovery-selected-" + uuid.uuid4().hex + ".zip")
    pipeline.cloud.download(recovered_uri, recovered, 80 * 1024 * 1024)
    output = root / ("identity-anchor-repaired-" + uuid.uuid4().hex + ".zip")
    partial = output.with_name(output.name + ".partial")
    try:
        with _maps(masks, metadata) as original, _maps(recovered, metadata) as repaired, zipfile.ZipFile(
                partial, "w", zipfile.ZIP_DEFLATED) as destination:
            contained, merge_report = _contained_overlap_labels(original, repaired, metadata, requests, tracks)
            raw_labels.update(contained)
            for frame in range(frames):
                source, additions = original(frame), repaired(frame)
                if any(int(label) not in {0, *raw_labels} for label in np.unique(additions)):
                    raise _failure()
                mapped = source.astype(np.uint16, copy=True)
                for request in requests:
                    region = additions == request["label"]
                    if not request["start_frame"] <= frame < request["end_frame"] and np.any(region):
                        raise _failure()
                    if frame == request["frame"]:
                        x, y = min(width - 1, int(request["x"] * width)), min(height - 1, int(request["y"] * height))
                        if not region[y, x]:
                            raise _failure()
                    if np.any(region & (source != 0) & (source != contained.get(request["label"], -1))):
                        raise _failure()
                    # Retain every original pixel and raw person ID. A proven
                    # partial/full containment only fills background around it.
                    mapped[region & (source == 0)] = raw_labels[request["label"]]
                buffer = BytesIO()
                Image.fromarray(mapped).save(buffer, "PNG")
                destination.writestr(f"labels/labels_{frame:05d}.png", buffer.getvalue())
        partial.replace(output)
        # Verify the actual taps after remapping, rather than trusting the report.
        with _maps(output, metadata) as read:
            for request in requests:
                if _pixel_label(read, request["frame"], request, width, height) != raw_labels[request["label"]]:
                    raise _failure()
        uri = pipeline.cloud.upload(pipeline.settings.worker_bucket, prefix + "raw-repaired-masks.zip", output, "application/zip")
        pipeline.update_job(job['id'], identity_anchor_recovery_merge_report=merge_report)
    except Exception:
        output.unlink(missing_ok=True)
        raise
    finally:
        partial.unlink(missing_ok=True)
    emit("cloudflare_initial_masks_recovered", job_id=job["id"], recovered_targets=len(requests),
         raw_labels_preserved=True, contained_partial_masks=len(merge_report))
    return output, uri, report
