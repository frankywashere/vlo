from io import BytesIO
import copy
from pathlib import Path
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.mask_cut_boundary import POLICY_VERSION, sanitize_cut_boundary_masks


def _png(labels):
    stream = BytesIO()
    Image.fromarray(labels.astype(np.uint16)).save(stream, 'PNG')
    return stream.getvalue()


def _write(path, images, *, omit=None):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for index, image in enumerate(images):
            if index != omit:
                archive.writestr(f'labels/labels_{index:05d}.png', _png(image))
        archive.writestr('objects.json', '{"diagnostic":"preserve-original"}')


def _read(path):
    with zipfile.ZipFile(path) as archive:
        return [np.array(Image.open(BytesIO(archive.read(name)))) for name in sorted(archive.namelist()) if name.endswith('.png')]


def _residue():
    mask = np.zeros((200, 200), dtype=bool)
    mask[50, 20:51] = True
    mask[50:81, 20] = True
    for y in range(95, 125, 3):
        for x in range(95, 125, 3):
            mask[y, x] = True
    return mask


def _case(tmp_path, *, frames=12, cut=4):
    images = [np.zeros((200, 200), dtype=np.uint16) for _ in range(frames)]
    for index, image in enumerate(images):
        image[30:160, 150:180] = 1
        if index < cut:
            image[30:80, 40:80] = 2
    images[cut][_residue()] = 2
    metadata = dict(width=200, height=200, frames=frames, duration=frames / 24,
                    anchor={'x': .25, 'y': .25, 'frame': 0, 'time': 0}, scene_cuts=[cut / 24])
    targets = [{'id': 'woman', 'anchor': copy.deepcopy(metadata['anchor'])}]
    return images, metadata, targets, tmp_path/'raw.zip', tmp_path/'filtered.zip'


def _filter(case, *, validated=True):
    images, metadata, targets, raw, output = case
    _write(raw, images)
    report = sanitize_cut_boundary_masks(raw, output, metadata,
        validated_cut_frames=[round(cut * 24) for cut in metadata['scene_cuts']] if validated else [], targets=targets)
    return report, _read(output)


def test_fragmented_cut_departure_is_removed_without_touching_any_other_pixels(tmp_path):
    case = _case(tmp_path)
    images, metadata, targets, raw, output = case
    source = [_png(image) for image in images]
    report, actual = _filter(case)
    assert report['policy_version'] == POLICY_VERSION
    assert [(item['frame'], item['label'], item['pixels']) for item in report['removed']] == [(4, 2, 161)]
    assert report['removed_pixels'] == 161
    assert report['source_sha256'] != report['output_sha256']
    for index, (before, after) in enumerate(zip(images, actual)):
        if index == 4:
            assert np.all(after[before == 2] == 0)
            assert np.array_equal(before[before != 2], after[before != 2])
        else:
            assert np.array_equal(before, after)
    with zipfile.ZipFile(output) as archive:
        assert archive.read('objects.json') == b'{"diagnostic":"preserve-original"}'
        assert all(archive.read(f'labels/labels_{index:05d}.png') == png for index, png in enumerate(source) if index != 4)
    assert all(np.array_equal(before, after) for before, after in zip(images, _read(raw)))


@pytest.mark.parametrize('change', ['coherent_tiny', 'coherent_occlusion', 'coherent_with_noise', 'dense_fragment',
    'reentry_next', 'reentry_third', 'reentry_later', 'dissolve', 'insufficient_following', 'prior_incoherent',
    'prior_not_large', 'prior_one_frame', 'first_frame_anchor', 'scene_anchor', 'unselected_id', 'outside_exact_cut'])
def test_coherent_brief_people_occlusions_reentries_and_uncertain_geometry_are_preserved(tmp_path, change):
    case = _case(tmp_path, frames=7 if change == 'insufficient_following' else 12,
                 cut=1 if change == 'prior_one_frame' else 4)
    images, metadata, targets, raw, output = case
    cut = round(metadata['scene_cuts'][0] * 24)
    if change in {'coherent_tiny', 'coherent_occlusion'}:
        images[cut][images[cut] == 2] = 0
        if change == 'coherent_tiny':
            images[cut][100:110, 80:92] = 2
        else:
            images[cut][100:102, 40:110] = 2
    elif change == 'coherent_with_noise':
        images[cut][10:25, 10:25] = 2
    elif change == 'dense_fragment':
        # A dense main body with many disconnected bits is not cut residue.
        images[cut][10:20, 10:20] = 2
    elif change.startswith('reentry_') or change == 'dissolve':
        offset = {'reentry_next': 1, 'reentry_third': 3, 'reentry_later': 6, 'dissolve': 1}[change]
        images[cut + offset][50:70, 50:70] = 2
    elif change == 'prior_incoherent':
        images[cut - 1][images[cut - 1] == 2] = 0
        images[cut - 1][_residue()] = 2
    elif change == 'prior_not_large':
        for frame in (cut - 2, cut - 1):
            images[frame][images[frame] == 2] = 0
            images[frame][40:60, 40:60] = 2
    elif change == 'first_frame_anchor':
        targets[0]['anchor'] = {'x': .1025, 'y': .2525, 'frame': cut, 'time': cut / 24}
    elif change == 'scene_anchor':
        targets[0]['scenes'] = [{'absent': False, 'anchor': {'x': .1025, 'y': .2525, 'frame': cut, 'time': cut / 24}}]
    elif change == 'unselected_id':
        targets[0]['anchor'] = {'x': .8, 'y': .3, 'frame': 0, 'time': 0}
    elif change == 'outside_exact_cut':
        images[cut][images[cut] == 2] = 0
        images[cut + 1][_residue()] = 2
    report, actual = _filter(case)
    assert report['removed'] == []
    assert all(np.array_equal(before, after) for before, after in zip(images, actual))


def test_unvalidated_scene_cut_is_never_enough_to_remove_pixels(tmp_path):
    case = _case(tmp_path)
    report, actual = _filter(case, validated=False)
    assert report['removed'] == []
    assert all(np.array_equal(before, after) for before, after in zip(case[0], actual))


def test_candidate_absence_cannot_borrow_frames_across_next_cut(tmp_path):
    case = _case(tmp_path)
    case[1]['scene_cuts'].append(7 / 24)
    report, _ = _filter(case)
    assert report['removed'] == []  # only two following frames in candidate shot


@pytest.mark.parametrize('failure', ['missing', 'dimension', 'invalid_cut', 'existing_output', 'same_output'])
def test_invalid_input_or_existing_diagnostic_is_never_overwritten(tmp_path, failure):
    images, metadata, targets, raw, output = _case(tmp_path)
    if failure == 'dimension':
        images[-1] = np.zeros((199, 200), dtype=np.uint16)
    _write(raw, images, omit=11 if failure == 'missing' else None)
    if failure == 'existing_output': output.write_bytes(b'preserve')
    if failure == 'same_output': output = raw
    original_raw = raw.read_bytes()
    with pytest.raises(PublicError):
        sanitize_cut_boundary_masks(raw, output, metadata,
            validated_cut_frames=[5 if failure == 'invalid_cut' else 4], targets=targets)
    assert raw.read_bytes() == original_raw
    if failure == 'existing_output': assert output.read_bytes() == b'preserve'
    elif failure != 'same_output': assert not output.exists()


# Real normalization geometry and the actual preparation orchestrator. No API
# calls or generation requests are allowed through this diagnostic harness.
from test_pipeline_v2 import harness, image_at, media  # noqa: E402,F401
from swapmein.pipeline import Pipeline  # noqa: E402


@pytest.mark.parametrize('model', ['minimax-h3', 'kling-4-0'])
@pytest.mark.parametrize('easy,enhanced', [(True, True), (True, False), (False, True)])
def test_initial_meta_filter_precedes_matching_and_manual_review_with_raw_diagnostics(
        tmp_path, monkeypatch, media, model, easy, enhanced):
    changed_media = dict(media)
    raw = tmp_path/'provider-masks.zip'
    with zipfile.ZipFile(media['masks']) as source, zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as destination:
        for frame in range(96):
            name = f'labels/labels_{frame:05d}.png'
            image = np.array(Image.open(BytesIO(source.read(name))))
            if frame >= 48:
                image[image == 7] = 0
            if frame == 48:
                image[50, 20:51] = 7
                image[50:81, 20] = 7
                for y in range(95, 131, 3):
                    for x in range(95, 131, 3): image[y, x] = 7
            destination.writestr(name, _png(image))
    changed_media['masks'] = raw
    matches = []
    def matching(self, job, video, masks, metadata, targets, root, remaining):
        matches.append(True)
        assert not np.any(image_at(masks.read_bytes(), 48) == 7)
        # The other person, and all non-cut PNGs, survive resizing/exclusion.
        assert np.any(image_at(masks.read_bytes(), 48) == 40000)
        raise PublicError('Independent identity ambiguity', 409, code='cloudflare_identity_uncertain')
    monkeypatch.setattr(Pipeline, 'match_cloudflare_identities', matching)
    job, cloud, _, _, _ = harness(monkeypatch, changed_media, easy=easy, enhanced=enhanced,
        selection='multi', audio_processing=False, preparation_provider='workers', sam_provider='meta',
        depth_guidance=False, generation_model=model, prompt_variant='cloudflare_identity_v1')
    assert job['status'] == 'tracking_review', job.get('message')
    assert matches == ([True] if easy else [])
    report = job['cut_boundary_mask_report']
    assert report['removed_pixels'] > 0
    assert [(item['frame'], item['label']) for item in report['removed']] == [(48, 7)]
    assert job['cut_boundary_raw_masks_uri'] != job['cut_boundary_working_raw_masks_uri']
    assert job['tracking_masks_uri'] == report['output_uri']
    assert report['input_uri'] == job['cut_boundary_working_raw_masks_uri']
    assert cloud.objects[job['cut_boundary_raw_masks_uri']] == raw.read_bytes()
    assert np.any(image_at(cloud.objects[report['input_uri']], 48) == 7)
    assert not np.any(image_at(cloud.objects[report['output_uri']], 48) == 7)
    assert not cloud.enqueued
    assert not any(endpoint == '/compose' for _, endpoint, _ in cloud.calls)
    if easy:
        assert job['identity_matching_error'] == 'cloudflare_identity_uncertain'
        assert not job['identity_matching_completed']
        assert not job['matching_fallback_used']


@pytest.mark.parametrize('provider,selection', [('workers', 'multi'), ('replicate', 'multi'), ('meta', 'all')])
def test_legacy_providers_and_group_union_bypass_cut_exclusion(monkeypatch, media, provider, selection):
    monkeypatch.setattr(Pipeline, 'filter_meta_cut_residue',
                        lambda *args, **kwargs: pytest.fail('This route is outside the Meta selected-person proof'))
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=False, enhanced=True,
        selection=selection, audio_processing=False, preparation_provider='workers' if provider == 'meta' else provider,
        sam_provider=provider, depth_guidance=False)
    assert job['status'] in {'tracking_review', 'ready'}, job.get('message')
    assert 'cut_boundary_mask_report' not in job


def test_meta_noop_preserves_archive_uri_and_does_not_add_upload(monkeypatch, media):
    job, cloud, _, _, _ = harness(monkeypatch, media, easy=False, enhanced=True,
        selection='multi', audio_processing=False, preparation_provider='workers', sam_provider='meta', depth_guidance=False)
    report = job['cut_boundary_mask_report']
    assert report['removed'] == [] and report['removed_pixels'] == 0
    assert report['input_uri'] == report['output_uri'] == job['tracking_masks_uri']
    assert report['source_sha256'] == report['output_sha256']
    assert not any('cut-boundary-masks-' in uri for uri in cloud.uploads)
