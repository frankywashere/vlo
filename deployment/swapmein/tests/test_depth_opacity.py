"""Depth remains the base beneath a light magenta tint."""
import importlib.util
from pathlib import Path
import numpy as np
import pytest


def test_twenty_percent_tint_preserves_depth_and_distinguishes_equal_maximum_hues(monkeypatch):
    worker = Path(__file__).parents[1] / 'worker_patch'
    monkeypatch.syspath_prepend(str(worker))
    spec = importlib.util.spec_from_file_location('depth_compositor_test', worker / 'composite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # The old max-channel conversion makes both of these the same depth.
    depth = np.array([[255, 20, 0], [255, 240, 140]], dtype=np.uint8)
    blended = module.depth_blend(depth, .2)
    assert not np.array_equal(blended[0], blended[1])
    assert np.array_equal(blended[:, 2], [43, 155])
    assert np.max(np.abs(blended.astype(int) - depth.astype(int))) <= 51
    assert np.array_equal(module.depth_blend(depth, 0), depth)


def test_actual_depth_composition_works_without_face_mesh(tmp_path, monkeypatch):
    import subprocess
    import zipfile
    from io import BytesIO
    from PIL import Image
    worker = Path(__file__).parents[1] / 'worker_patch'
    monkeypatch.syspath_prepend(str(worker))
    spec = importlib.util.spec_from_file_location('depth_video_test', worker / 'composite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, color in [('source', 'red'), ('depth', 'yellow')]:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color={color}:s=32x32:r=24:d=1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(tmp_path / f'{name}.mp4')], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                    '-t', '1', str(tmp_path / 'audio.wav')], check=True)
    labels = np.zeros((32, 32), dtype=np.uint16); labels[:, 16:] = 1
    image = BytesIO(); Image.fromarray(labels).save(image, 'PNG')
    with zipfile.ZipFile(tmp_path / 'masks.zip', 'w') as archive:
        for frame in range(24): archive.writestr(f'labels/labels_{frame:05d}.png', image.getvalue())
    metrics = module.compose(tmp_path / 'source.mp4', tmp_path / 'masks.zip', tmp_path / 'audio.wav',
        tmp_path / 'guide.mp4', 1, 'depth', tmp_path / 'depth.mp4',
        [{'label': 1, 'color': '#00dcff'}], depth_opacity=.2)
    assert metrics['depth_opacity'] == .2 and metrics['face_mesh'] is False
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(tmp_path / 'guide.mp4'),
        '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
    pixels = np.frombuffer(raw, dtype=np.uint8).reshape(32, 32, 3)
    assert pixels[16, 5, 0] > 240  # Background preserved.
    assert pixels[16, 25, 0] > 240
    assert pixels[16, 25, 1] > 200  # Yellow depth remains the base, not source red.
    assert 25 < pixels[16, 25, 2] < 65  # Light magenta overlay.


@pytest.mark.parametrize("identity_tints", [False, True])
def test_depth_video_keeps_eighty_percent_depth_under_each_persons_tint(tmp_path, monkeypatch, identity_tints):
    import subprocess
    import zipfile
    from io import BytesIO
    from PIL import Image

    worker = Path(__file__).parents[1] / 'worker_patch'
    monkeypatch.syspath_prepend(str(worker))
    spec = importlib.util.spec_from_file_location('depth_identity_video_test', worker / 'composite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name, color in [('source', 'red'), ('depth', 'yellow')]:
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color={color}:s=64x32:r=24:d=1',
                        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(tmp_path / f'{name}.mp4')], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=44100:cl=stereo',
                    '-t', '1', str(tmp_path / 'audio.wav')], check=True)
    labels = np.zeros((32, 64), dtype=np.uint16)
    labels[:, 16:32] = 1
    labels[:, 40:56] = 2
    image = BytesIO()
    Image.fromarray(labels).save(image, 'PNG')
    with zipfile.ZipFile(tmp_path / 'masks.zip', 'w') as archive:
        for frame in range(24):
            archive.writestr(f'labels/labels_{frame:05d}.png', image.getvalue())
    styles = [{'label': 1, 'color': '#00dcff'}, {'label': 2, 'color': '#ff4fd8'}]
    kwargs = {'depth_tint_by_target': True} if identity_tints else {}
    metrics = module.compose(tmp_path / 'source.mp4', tmp_path / 'masks.zip', tmp_path / 'audio.wav',
        tmp_path / 'guide.mp4', 1, 'depth', tmp_path / 'depth.mp4', styles, depth_opacity=.2, **kwargs)

    def frame(path):
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path),
            '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
        return np.frombuffer(raw, dtype=np.uint8).reshape(32, 64, 3).astype(int)

    source, depth, guide = (frame(tmp_path / f'{name}.mp4') for name in ('source', 'depth', 'guide'))
    assert metrics['depth_opacity'] == .2
    assert metrics['depth_tint_by_target'] is identity_tints
    assert metrics['face_mesh'] is False
    assert np.max(np.abs(guide[16, 5] - source[16, 5])) <= 8
    for x, rgb in [(24, [0, 220, 255]), (48, [255, 79, 216])]:
        tint = rgb if identity_tints else [255, 79, 216]
        expected = np.rint(depth[16, x] * .8 + np.array(tint) * .2)
        # Tolerate H.264/YUV rounding; the two expected identities differ far more.
        assert np.max(np.abs(guide[16, x] - expected)) <= 8
    if identity_tints:
        assert guide[16, 48, 0] - guide[16, 24, 0] > 35
    else:
        assert np.max(np.abs(guide[16, 24] - guide[16, 48])) <= 8


@pytest.mark.parametrize("fill,styles", [
    ('depth', None), ('depth', []), ('translucent', [{'label': 1, 'color': '#00dcff'}]),
])
def test_identity_depth_tint_rejects_incompatible_guides_before_media_work(tmp_path, monkeypatch, fill, styles):
    worker = Path(__file__).parents[1] / 'worker_patch'
    monkeypatch.syspath_prepend(str(worker))
    spec = importlib.util.spec_from_file_location('depth_identity_invalid_test', worker / 'composite.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'probe', lambda *_: pytest.fail('Invalid tint mode must not read media'))
    with pytest.raises(ValueError, match='Identity depth tint requires'):
        module.compose(tmp_path / 'source.mp4', tmp_path / 'masks.zip', tmp_path / 'audio.wav',
            tmp_path / 'guide.mp4', 1, fill, tmp_path / 'depth.mp4', styles,
            depth_opacity=.2, depth_tint_by_target=True)
