"""CPU face landmarks from RGB, conservatively associated with current masks."""
from collections import Counter
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw

MODEL = Path('/models/face_landmarker.task')


def face_label(face, labels, eligible):
    height, width = labels.shape
    coordinates = np.asarray([(point.x, point.y) for point in face], dtype=float)
    if not len(coordinates) or not np.isfinite(coordinates).all():
        return None
    valid = ((coordinates >= 0) & (coordinates < 1)).all(axis=1)
    if valid.sum() < len(face) * .8:
        return None
    points = coordinates[valid]
    values = labels[(points[:, 1] * height).astype(int), (points[:, 0] * width).astype(int)]
    counts = Counter(int(value) for value in values)
    matches = [label for label in eligible if counts[label] >= len(face) * .6]
    return matches[0] if len(matches) == 1 else None


class FaceMeshPainter:
    def __init__(self, *, model=MODEL, multiple_per_label=False):
        import mediapipe as mp
        self.mp = mp
        self.multiple_per_label = multiple_per_label
        options = mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(model), delegate=mp.tasks.BaseOptions.Delegate.CPU),
            running_mode=mp.tasks.vision.RunningMode.IMAGE, num_faces=32,
            min_face_detection_confidence=.5, min_face_presence_confidence=.5)
        self.detector = mp.tasks.vision.FaceLandmarker.create_from_options(options)
        connections = mp.tasks.vision.FaceLandmarksConnections
        self.lines = [(connections.FACE_LANDMARKS_TESSELATION, (50, 170, 205), 1),
                      (connections.FACE_LANDMARKS_LIPS, (255, 135, 100), 2),
                      (connections.FACE_LANDMARKS_LEFT_EYE, (130, 255, 150), 2),
                      (connections.FACE_LANDMARKS_RIGHT_EYE, (130, 255, 150), 2)]
        self.metrics = dict(frames=0, frames_with_mesh=0, faces_detected=0, faces_drawn=0, unmatched_faces=0,
                            ambiguous_faces=0, processing_seconds=0)

    def apply(self, original, composed, labels, target):
        started = time.monotonic()
        self.metrics['frames'] += 1
        if not target.any():
            return
        result = self.detector.detect(self.mp.Image(image_format=self.mp.ImageFormat.SRGB,
                                                   data=np.ascontiguousarray(original)))
        height, width = labels.shape
        eligible = set(int(value) for value in np.unique(labels[target])) - {0}
        matched = [(face, face_label(face, labels, eligible))
                   for face in result.face_landmarks]
        counts = Counter(label for _, label in matched if label is not None)
        self.metrics['faces_detected'] += len(matched)
        layer = Image.new('RGB', (width, height))
        draw = ImageDraw.Draw(layer)
        drawn = 0
        for face, label in matched:
            if label is None:
                self.metrics['unmatched_faces'] += 1
                continue
            if not self.multiple_per_label and counts[label] > 1:
                self.metrics['ambiguous_faces'] += 1
                continue
            points = [(round(point.x * width), round(point.y * height)) for point in face]
            for connections, color, line_width in self.lines:
                for connection in connections:
                    draw.line([points[connection.start], points[connection.end]], fill=color, width=line_width)
            drawn += 1
        overlay = np.asarray(layer)
        pixels = np.any(overlay != 0, axis=2)
        pixels &= target
        composed[pixels] = overlay[pixels]
        self.metrics['faces_drawn'] += drawn
        self.metrics['frames_with_mesh'] += int(drawn > 0)
        self.metrics['processing_seconds'] += time.monotonic() - started

    def close(self):
        self.detector.close()
