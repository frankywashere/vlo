import importlib.util
import json
from io import BytesIO
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.media import probe
from swapmein.provider import generation_payload


def test_prompt_identifies_cyan_overlay_and_preserves_legacy_gray_guides():
    new = generation_payload(5, 'image', 'video', True)['prompt']
    assert 'translucent cyan' in new and 'scene changes' in new and 'gray' not in new
    legacy = generation_payload(5, 'image', 'video', True, mask_style='gray')['prompt']
    assert 'gray silhouette' in legacy


def test_depth_prompt_identifies_only_selected_person():
    prompt = generation_payload(5, 'image', 'video', True, mask_style='depth')['prompt']
    assert 'selected person covered by colored depth imagery' in prompt
    assert 'Keep all other people unchanged' in prompt
    assert 'cyan' not in prompt and 'gray silhouette' not in prompt


@pytest.mark.parametrize("fill_mode,opacity", [("color", .5), ("translucent", .2)])
def test_color_compositor_changes_only_selected_label_and_allows_missing_scene(tmp_path, monkeypatch, fill_mode, opacity):
    patch = Path(__file__).parents[1] / 'worker_patch/composite.py'
    monkeypatch.setitem(sys.modules, 'common', SimpleNamespace(probe=probe))
    spec = importlib.util.spec_from_file_location('tested_color_composite', patch)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source, audio, archive, output = [tmp_path / s for s in ('source.mp4', 'vocals.wav', 'masks.zip', 'output.mp4')]
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=96x64:r=24:d=1',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(source)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=0.95', str(audio)], check=True)
    with zipfile.ZipFile(archive, 'w') as z:
        for index in range(24):
            labels = np.zeros((64, 96), dtype=np.uint16)
            labels[10:50, 10:35] = 1  # unselected person stays untouched
            if index < 12: labels[10:50, 60:85] = 2
            raw = BytesIO(); Image.fromarray(labels).save(raw, format='PNG')
            z.writestr(f'labels/labels_{index:05d}.png', raw.getvalue())
    metrics = module.compose(source, archive, audio, output, 2, fill_mode)
    assert metrics['mask_pixels_per_frame'][12:] == [0] * 12
    assert metrics['overlay_rgb'] == [0, 220, 255]
    assert metrics['overlay_opacity'] == opacity
    def frames(path):
        data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        return np.frombuffer(data, np.uint8).reshape(-1, 64, 96, 3)
    original, actual = frames(source), frames(output)
    assert actual.shape == original.shape
    assert np.abs(actual[:,20:40,15:30].astype(float)-original[:,20:40,15:30]).mean() < 5
    assert np.abs(actual[12:].astype(float)-original[12:]).mean() < 5
    expected = original[0,25,70].astype(float)*(1-opacity) + np.array([0,220,255])*opacity
    assert np.abs(actual[0,25,70].astype(float)-expected).max() < 8
    assert float(next(s for s in probe(output)['streams'] if s['codec_type']=='audio')['duration']) >= .99


@pytest.mark.parametrize("use_source_audio", [False, True])
def test_depth_compositor_replaces_only_selected_pixels_and_preserves_missing_frames(tmp_path, monkeypatch, use_source_audio):
    patch = Path(__file__).parents[1] / 'worker_patch/composite.py'
    monkeypatch.setitem(sys.modules, 'common', SimpleNamespace(probe=probe))
    spec = importlib.util.spec_from_file_location('tested_depth_composite', patch)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source, depth, audio, archive, output = [tmp_path / s for s in ('source.mp4', 'depth.mp4', 'vocals.wav', 'masks.zip', 'output.mp4')]
    for path, color in ((source, 'red'), (depth, 'blue')):
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=c={color}:s=96x64:r=24:d=1',
            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', str(path)], check=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=duration=0.95', str(audio)], check=True)
    with zipfile.ZipFile(archive, 'w') as z:
        for index in range(24):
            labels = np.zeros((64, 96), dtype=np.uint16)
            labels[10:50, 10:35] = 1
            if index < 12: labels[10:50, 60:85] = 2
            raw = BytesIO(); Image.fromarray(labels).save(raw, format='PNG')
            z.writestr(f'labels/labels_{index:05d}.png', raw.getvalue())
    if use_source_audio:
        with_audio = tmp_path/'original-audio.mp4'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(source),'-f','lavfi','-i','sine=duration=1',
                        '-map','0:v:0','-map','1:a:0','-c:v','copy','-c:a','aac','-t','1',str(with_audio)],check=True)
        audio = with_audio  # MP4 input, exactly like bypass-mode cloud requests.
    metrics = module.compose(source, archive, audio, output, 2, 'depth', depth)
    assert metrics['depth'] is True and metrics['face_mesh'] is False
    assert metrics['mask_pixels_per_frame'][12:] == [0] * 12
    def frames(path):
        data = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        return np.frombuffer(data, np.uint8).reshape(-1, 64, 96, 3).astype(float)
    original, colored, actual = frames(source), frames(depth), frames(output)
    assert np.abs(actual[:,20:40,15:30]-original[:,20:40,15:30]).mean() < 5
    assert np.abs(actual[12:]-original[12:]).mean() < 5
    assert np.abs(actual[0,25,70]-colored[0,25,70]).max() < 8


def test_flat_multi_mask_prompt_does_not_claim_depth():
    targets = [{"reference_index": 0, "anchor": {"x": .2, "y": .4, "time": 0}},
               {"reference_index": 1, "anchor": {"x": .8, "y": .4, "time": 0}}]
    prompt = generation_payload(5, ['a', 'b'], 'video', True, mask_style='solid_multi', targets=targets)['prompt']
    assert 'depth' not in prompt.lower()
    assert 'cyan' in prompt and 'magenta' in prompt and 'flat' in prompt
