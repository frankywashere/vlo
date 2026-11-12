"""Exact guide copy, bounded private access and generation-safe expiry."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from types import SimpleNamespace

import pytest
from google.api_core.exceptions import NotFound, PreconditionFailed

from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.guide_retention import retain_guide, retained_guide_view, delete_expired_guides
from test_api import MemoryRecords

JOB, ACCOUNT = 'a' * 32, 'b' * 64
SOURCE = f'gs://app/outputs/{JOB}/guide.mp4'


@pytest.fixture
def retained(monkeypatch):
    clock = {'now': 1000.0}
    monkeypatch.setattr('swapmein.guide_retention.time.time', lambda: clock['now'])
    settings = replace(Settings(), app_bucket='app')
    records = MemoryRecords()
    job = dict(id=JOB, account_id=ACCOUNT, status='completed', completed_at=1000,
               guide_uri=SOURCE, setup={}, provider_billing={'provider_cost_usd': .5})
    records.create(f'jobs/{JOB}.json', job)
    records.list_paths = lambda prefix, maximum=25: sorted(k for k in records.data if k.startswith(prefix))[:maximum]
    records.delete = lambda path: records.data.pop(path, None)
    objects = {SOURCE: {'data': b'exact submitted guide', 'generation': 7}}
    deleted, copies = [], []
    class Blob:
        def __init__(self, uri): self.uri = uri
        def reload(self, **kwargs):
            if self.uri not in objects: raise NotFound('missing')
            self.size = len(objects[self.uri]['data']); self.generation = objects[self.uri]['generation']
        def delete(self, *, if_generation_match, **kwargs):
            self.reload()
            if self.generation != if_generation_match: raise PreconditionFailed('replaced')
            deleted.append(self.uri); del objects[self.uri]
    def download(uri, path, maximum):
        assert len(objects[uri]['data']) <= maximum
        path.write_bytes(objects[uri]['data'])
    def copy(uri, bucket, key):
        target = f'gs://{bucket}/{key}'
        if target in objects: raise PreconditionFailed('exists')
        objects[target] = deepcopy(objects[uri]); copies.append(target)
        return target
    cloud = SimpleNamespace(settings=settings, download=download, copy=copy,
        blob=lambda bucket, key: Blob(f'gs://{bucket}/{key}'),
        signed_url=lambda uri, seconds=3600: f'https://private.test/{seconds}')
    return SimpleNamespace(settings=settings, records=records, job=job, cloud=cloud,
                           objects=objects, deleted=deleted, copies=copies, clock=clock)


def test_retains_exact_guide_and_retries_never_extend_expiry(retained):
    t = retained
    saved = retain_guide(t.settings, t.records, t.cloud, t.job)
    guide = saved['retained_guide']
    assert guide['sha256'] == hashlib.sha256(b'exact submitted guide').hexdigest()
    assert guide['expires_at'] == 87400 and guide['bytes'] == len(b'exact submitted guide')
    assert t.objects[guide['uri']]['data'] == t.objects[SOURCE]['data']
    assert saved['provider_billing'] == t.job['provider_billing']
    t.clock['now'] += 100
    assert retain_guide(t.settings, t.records, t.cloud, saved)['retained_guide'] == guide
    assert len(t.copies) == 1
    assert retained_guide_view(saved, t.cloud, now=87398)['guide_url'].endswith('/2')
    assert retained_guide_view(saved, t.cloud, now=87400) == {}


def test_retains_actual_silent_submission_not_the_audible_preview(retained):
    t = retained; uri = f'gs://app/outputs/{JOB}/silent-input.mp4'
    t.objects[uri] = {'data': b'exact silent submitted guide', 'generation': 8}
    t.job.update(setup={'submit_without_audio': True}, silent_submission={'guide_uri': SOURCE, 'uri': uri})
    t.records.data[f'jobs/{JOB}.json'] = deepcopy(t.job)
    saved = retain_guide(t.settings, t.records, t.cloud, t.job)
    assert t.objects[saved['retained_guide']['uri']]['data'] == b'exact silent submitted guide'
    assert len(saved['retained_guide']['expiring_sources']) == 2


def test_test_site_ready_guide_has_the_same_one_day_retention(retained):
    t = retained; t.job.update(status='ready', preparation_only=True); t.job.pop('completed_at')
    t.records.data[f'jobs/{JOB}.json'] = deepcopy(t.job)
    result = retain_guide(t.settings, t.records, t.cloud, t.job)
    assert result['status'] == 'ready' and result['retained_guide']['expires_at'] == 87400


def test_guide_only_silent_option_exposes_the_same_video_input_without_generation(retained, monkeypatch):
    t = retained; t.job.update(status='ready', preparation_only=True, setup={'submit_without_audio': True})
    t.records.data[f'jobs/{JOB}.json'] = deepcopy(t.job)
    monkeypatch.setattr('swapmein.media.strip_audio', lambda source, output: output.write_bytes(b'silent video'))
    def upload(bucket, key, path, content_type):
        uri = f'gs://{bucket}/{key}'; t.objects[uri] = {'data': path.read_bytes(), 'generation': 12}
        return uri
    t.cloud.upload = upload
    saved = retain_guide(t.settings, t.records, t.cloud, t.job)
    assert saved['silent_submission']['guide_uri'] == SOURCE
    assert t.objects[saved['retained_guide']['uri']]['data'] == b'silent video'
    assert len(saved['retained_guide']['expiring_sources']) == 2


def test_expiry_deletes_only_this_guide_and_preserves_library_and_other_jobs(retained):
    t = retained; saved = retain_guide(t.settings, t.records, t.cloud, t.job)
    other = 'gs://app/library/protected.mp4'; t.objects[other] = {'data': b'original and final', 'generation': 4}
    assert delete_expired_guides(t.settings, t.records, t.cloud, now=87399)['deleted'] == 0
    assert not t.deleted
    assert delete_expired_guides(t.settings, t.records, t.cloud, now=87400)['deleted'] == 1
    assert set(t.deleted) == {SOURCE, saved['retained_guide']['uri']}
    assert other in t.objects and not t.records.list_paths('guide-expiry/')
    assert delete_expired_guides(t.settings, t.records, t.cloud, now=87401)['deleted'] == 0


def test_expiry_does_not_delete_replaced_generation(retained):
    t = retained; saved = retain_guide(t.settings, t.records, t.cloud, t.job)
    t.objects[saved['retained_guide']['uri']]['generation'] = 99
    result = delete_expired_guides(t.settings, t.records, t.cloud, now=87400)
    assert result['errors'] == 1 and not t.deleted


@pytest.mark.parametrize('bad', ['source', 'expiry_source'])
def test_job_scoping_prevents_copying_or_deleting_unrelated_objects(retained, bad):
    t = retained
    if bad == 'source':
        t.job['guide_uri'] = 'gs://app/library/someone-else.mp4'
        with pytest.raises(PublicError): retain_guide(t.settings, t.records, t.cloud, t.job)
    else:
        retain_guide(t.settings, t.records, t.cloud, t.job)
        marker = t.records.list_paths('guide-expiry/')[0]
        t.records.data[marker]['expiring_sources'][0]['uri'] = 'gs://app/library/someone-else.mp4'
        assert delete_expired_guides(t.settings, t.records, t.cloud, now=87400)['errors'] == 1
    assert not t.deleted


def test_old_cleaned_jobs_do_not_attempt_reconstruction(retained):
    t = retained; t.job['temporary_media_cleanup_started_at'] = 900
    assert retain_guide(t.settings, t.records, t.cloud, t.job) == t.job
    assert not t.copies and not t.records.list_paths('guide-expiry/')
