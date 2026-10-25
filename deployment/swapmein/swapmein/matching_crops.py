"""Shot-local matching geometry; never modify the masks used for rendering."""
import numpy as np


def dominant_region(labels, label):
    """Find the largest 8-connected component using row runs, without OpenCV.

    Tiny disconnected SAM islands must not expand a shoulder crop to include
    another person's face. Run-length components keep this bounded by mask
    geometry rather than a Python object for every foreground pixel.
    """
    parents, sizes, boxes = [], [], []

    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    previous = []
    for y, row in enumerate(labels):
        changes = np.flatnonzero(np.diff(np.r_[False, row == label, False]))
        current, cursor = [], 0
        for left, right in zip(changes[::2], changes[1::2]):
            left, right = int(left), int(right)
            index = len(parents)
            parents.append(index)
            sizes.append(right - left)
            boxes.append([left, y, right, y + 1])
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
                    boxes[a] = [min(boxes[a][0], boxes[b][0]), min(boxes[a][1], boxes[b][1]),
                                max(boxes[a][2], boxes[b][2]), max(boxes[a][3], boxes[b][3])]
            current.append((left, right, index))
        previous = current
    roots = [index for index in range(len(parents)) if parents[index] == index]
    if not roots:
        return None
    largest = max(roots, key=lambda index: sizes[index])
    return {"box": tuple(boxes[largest]), "pixels": sizes[largest],
            "discarded_pixels": sum(sizes[index] for index in roots if index != largest)}


def sample_frames(samples, start, end):
    """At most three separated observations, unaffected by one-frame strays.

    Each observation has its own visible candidate set. Requiring every track
    in every observation can force all matching onto a single cut-boundary
    frame containing a transient SAM ID.
    """
    eligible = [frame for frame, _ in samples]
    if not eligible:
        return []
    middle = (start + end - 1) / 2
    selected = [min(eligible, key=lambda frame: (abs(frame - middle), frame))]
    # Then inspect opposite portions of the shot, avoiding adjacent frames when
    # enough footage exists. No frame is borrowed from across a cut.
    for fraction in (.25, .75):
        remaining = [frame for frame in eligible if frame not in selected]
        if not remaining:
            break
        preferred = start + (end - start - 1) * fraction
        selected.append(min(remaining, key=lambda frame: (abs(frame - preferred), frame)))
    return selected
