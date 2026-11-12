"""Offline orchestration proofs: new evidence never grants paid generation."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from types import SimpleNamespace
import time
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.mask_quality import _png, _write
from swapmein.pipeline import Pipeline
from swapmein.storage import preparation_leases
from test_api import api, authenticated, ready_job  # noqa: F401


class MemoryRecords:
    def __init__(self, job):
        self.data = deepcopy(job)
        self.history = []
        self.crash_before_targeted_write = False

    def update(self, path, callback):
        assert path == f"jobs/{self.data['id']}.json"
        following = deepcopy(callback(deepcopy(self.data)))
        if self.crash_before_targeted_write and following.get('tracking_plan',{}).get('version')==4:
            self.crash_before_targeted_write=False
            raise RuntimeError('interrupted before targeted review persisted')
        self.data = following
        self.history.append(deepcopy(self.data))
        return deepcopy(self.data)


class OfflineCloud:
    def __init__(self, archive):
        self.objects = {'original': archive, 'source': b'original-rgb-fixture',
                        'composed': b'cpu-guide-fixture'}
        self.downloads, self.uploads, self.compositions, self.enqueued = [], [], [], []
        self.allow_compose = False

    def download(self, uri, path, limit=None):
        self.downloads.append(uri)
        path.write_bytes(self.objects[uri])

    def upload(self, bucket, key, path, mime):
        assert key not in self.objects
        self.objects[key] = path.read_bytes()
        self.uploads.append(key)
        return key

    @contextmanager
    def worker(self, kind):
        assert self.allow_compose, 'A guide was composed before identity review finished'
        def invoke(endpoint, payload, remaining):
            assert endpoint == '/compose'
            self.compositions.append(deepcopy(payload))
            return {'run_id': payload['run_id']}
        yield invoke

    def worker_output(self, result, run_id, name):
        assert name == 'prepared_guide.mp4'
        return 'composed'

    def copy(self, source, bucket, key):
        self.objects[key] = self.objects[source]
        return key

    def enqueue(self, *args, **kwargs):
        self.enqueued.append((args, kwargs))
        pytest.fail('Mask checking or source review must not enqueue generation')


def archive_arrays(payload):
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        return [np.asarray(Image.open(BytesIO(archive.read(name))))
                for name in sorted(archive.namelist())]


@pytest.fixture
def example(tmp_path, monkeypatch):
    width, height, frames = 96, 64, 6
    maps = []
    for frame in range(frames):
        labels = np.zeros((height, width), np.uint16)
        if frame < 3:
            labels[10:55, 10:30] = 1
        else:
            labels[8:52, 38:58] = 1
        labels[10:55, 65:85] = 2
        maps.append(labels)
    archive = BytesIO()
    with zipfile.ZipFile(archive, 'w') as zipped:
        for frame, labels in enumerate(maps):
            _write(zipped, f'labels/labels_{frame:05d}.png', _png(labels))
    targets = [dict(id='one', name='Person 1', reference_index=0,
                    anchor=dict(x=20.5/width, y=30.5/height, time=0)),
               dict(id='two', name='Person 2', reference_index=1,
                    anchor=dict(x=75.5/width, y=30.5/height, time=0))]
    sections = [dict(id='scene-'+t['id'], target_id=t['id'], start_frame=0,
                     end_frame=frames, start=0, end=frames/24, needs_review=True)
                for t in targets]
    choices = [dict(segment_id=s['id'], absent=False, anchor=deepcopy(t['anchor']))
               for s,t in zip(sections, targets)]
    assignments = [dict(segment_id=s['id'], target_id=s['target_id'], start_frame=0,
                        end_frame=frames, selected_label=i+1, absent=False)
                   for i,s in enumerate(sections)]
    job = dict(id='recheck-runtime', status='preparing', tracking_revision=1,
        tracking_applied_revision=1, tracking_metadata=dict(width=width, height=height,
            frames=frames, duration=frames/24, anchor={**targets[0]['anchor'], 'frame':0}, scene_cuts=[]),
        tracking_plan=dict(version=2, targets=deepcopy(targets), segments=sections),
        tracking_choices=choices, tracking_assignments=assignments,
        normalized_targets=[{**deepcopy(t), 'scenes':[dict(start=0,end=frames/24,
            absent=False,anchor=deepcopy(t['anchor']))]} for t in targets],
        normalized_uri='source', reviewed_masks_uri='original', tracking_vocals_uri='vocals',
        guide_uri='old-guide', generation_model='minimax-h3', setup=dict(auto_generate=True),
        generation_after_review_revision=1, capacity_generation_authorized=True,
        capacity_approval_revision=1, generation_capacity_token='old-capacity',
        review_generation_queued=True, mask_quality_approved_sha256='old-guide-sha',
        mask_quality_approved_revision=1)
    records, cloud = MemoryRecords(job), OfflineCloud(archive.getvalue())
    pipeline = Pipeline(replace(Settings(),worker_bucket='workers',app_bucket='app'),records,cloud)
    calls = []
    outcome = {'value':'confirmed'}

    def helper(video, masks, metadata, candidates, **kwargs):
        calls.append(deepcopy(candidates))
        assert video.read_bytes() == b'original-rgb-fixture'
        assert metadata == job['tracking_metadata']
        assert kwargs['review_revision'] >= 1 and kwargs['budget'].job_id == job['id']
        assert len(candidates) == 1 and candidates[0]['target_id'] == 'one'
        if isinstance(outcome['value'], BaseException):
            raise outcome['value']
        return dict(evidence=[{**deepcopy(c), 'outcome':outcome['value']} for c in candidates],
                    requests=1, usage=dict(input_tokens=10,estimated_cost_usd=.00001))

    monkeypatch.setattr('swapmein.cloudflare_credentials.server_cloudflare_credentials',lambda:object())
    monkeypatch.setattr('swapmein.cloudflare_budget.MatchingBudget',lambda records,job_id:SimpleNamespace(job_id=job_id))
    monkeypatch.setattr('swapmein.mask_identity_rechecks.recheck_mask_identities',helper)
    monkeypatch.setattr('swapmein.cloudflare_matching.requests.post',lambda *a,**k:pytest.fail('No live network'))
    return SimpleNamespace(job=job, records=records, cloud=cloud, pipeline=pipeline,
                           calls=calls, outcome=outcome, maps=maps, tmp_path=tmp_path)


def assert_no_old_approval(job):
    assert job['generation_after_review_revision'] is None
    assert not job['capacity_generation_authorized']
    assert job['capacity_approval_revision'] is None
    assert job['generation_capacity_token'] is None
    assert not job['review_generation_queued']
    assert job['mask_quality_approved_sha256'] is None
    assert job['mask_quality_approved_revision'] is None


def assert_other_person_unchanged(example, job):
    output = archive_arrays(example.cloud.objects[job['reviewed_masks_uri']])
    for original,checked in zip(example.maps,output):
        assert np.array_equal(original==2,checked==2)
        assert not np.any((checked==1)&(original!=1))
    return output


def test_confirmed_component_supplements_baseline_and_requires_fresh_guide_approval(example):
    result = example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    output = assert_other_person_unchanged(example,result)
    assert all(np.array_equal(a,b) for a,b in zip(example.maps,output))
    assert len(example.calls)==1 and len(result['mask_supplemental_anchors'])==1
    assert result['mask_identity_recheck_state']['status']=='completed'
    assert result['reviewed_mask_quality']['identity_rechecks']['confirmed']==1
    assert result['reviewed_mask_quality']['review_required']
    assert result['tracking_plan']==example.job['tracking_plan']
    assert result['tracking_assignments']==example.job['tracking_assignments']
    assert_no_old_approval(result)
    assert not example.cloud.compositions and not example.cloud.enqueued


@pytest.mark.parametrize('outcome',['uncertain','unavailable'])
def test_unresolved_result_opens_only_targeted_v4_and_never_composes_or_generates(example,outcome):
    example.outcome['value']=outcome
    example.pipeline.rebuild_reviewed_guide(deepcopy(example.job),lambda:90)
    result=example.records.data
    assert result['status']=='tracking_review' and result['tracking_plan']['version']==4
    assert [t['id'] for t in result['tracking_plan']['targets']]==['one']
    assert len(result['tracking_plan']['segments'])==1
    assert result['tracking_choices']==result['tracking_assignments']==[]
    assert result['tracking_revision']==2
    base=result['targeted_mask_review']
    assert base['pending'] and base['base_tracking_plan']==example.job['tracking_plan']
    assert base['base_tracking_choices']==example.job['tracking_choices']
    assert base['base_tracking_assignments']==example.job['tracking_assignments']
    assert result['guide_uri']=='old-guide'
    assert_other_person_unchanged(example,result)
    assert_no_old_approval(result)
    assert not example.cloud.compositions and not example.cloud.enqueued


def test_interrupted_started_claim_never_submits_again_and_reopens_focused_review(example):
    example.outcome['value']=RuntimeError('worker interrupted after request intent')
    with pytest.raises(RuntimeError,match='worker interrupted'):
        example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    interrupted=deepcopy(example.records.data)
    assert interrupted['mask_identity_recheck_state']['status']=='started'
    assert len(example.calls)==1
    # The same worker/task retry can only use the durable intent, not POST again.
    example.pipeline.rebuild_reviewed_guide(interrupted,lambda:90)
    assert len(example.calls)==1
    assert example.records.data['status']=='tracking_review'
    assert example.records.data['tracking_plan']['version']==4
    receipt=example.records.data['reviewed_mask_quality']['identity_rechecks']
    assert receipt['requests'] is None and not receipt['request_count_known']
    assert receipt['usage']['cost_status']=='unknown_after_interruption'
    assert_no_old_approval(example.records.data)
    assert not example.cloud.compositions and not example.cloud.enqueued


def test_wrong_fragment_tap_returns_to_editable_same_check_without_paid_retry(api,example,monkeypatch):
    import swapmein.app as gateway
    example.outcome['value']='uncertain'
    review=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    client,records,cloud,provider=api
    headers,owner=authenticated(api)
    identifier=ready_job(records,owner);path=f'jobs/{identifier}.json'
    records.data[path].update({k:deepcopy(v) for k,v in review.items() if k!='id'})
    current=records.data[path];candidate=current['targeted_mask_review']['checks'][0]
    section=current['tracking_plan']['segments'][0]
    bad={**candidate['anchor'],'x':75.5/96,'y':30.5/64}  # Other selected person's mask.
    payload=dict(revision=current['tracking_revision'],choices=[dict(segment_id=section['id'],absent=False,anchor=bad)])
    assert client.post(f'/api/jobs/{identifier}/tracking-review',headers=headers,json=payload).status_code==202
    token=records.data[path]['tracking_task_token']
    monkeypatch.setattr(cloud,'download',lambda uri,destination,maximum:destination.write_bytes(example.cloud.objects[uri]))
    cloud.shutdown_verified=True
    Pipeline(gateway.settings,records,cloud).prepare(identifier,token)
    retriable=records.data[path]
    assert retriable['status']=='tracking_review' and retriable['tracking_review_error']
    assert retriable['tracking_applied_revision']==review['targeted_mask_review']['base_tracking_applied_revision']
    assert retriable['tracking_error_segment_id']==section['id']
    payload['choices'][0]['anchor']=candidate['anchor']
    assert client.post(f'/api/jobs/{identifier}/tracking-review',headers=headers,json=payload).status_code==202
    assert len(example.calls)==1 and not provider.submissions


def test_completed_receipt_replay_and_stale_concurrent_claim_do_not_duplicate_requests(example):
    first=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    key=first['mask_identity_recheck_state']['key']
    # An already-running second caller holds the old pre-claim job snapshot.
    second=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    assert len(example.calls)==1
    assert second['mask_identity_recheck_state']['key']==key
    assert second['mask_identity_recheck_state']['status']=='completed'
    assert_other_person_unchanged(example,second)
    assert_no_old_approval(second)


def test_public_service_failure_is_durable_stopped_and_never_retried(example):
    example.outcome['value']=PublicError('service temporarily failed',503,code='cloudflare_matching_unavailable')
    first=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    assert first['mask_identity_recheck_state']['status']=='stopped'
    assert first['tracking_plan']['version']==4 and len(example.calls)==1
    # A stale parallel caller must also observe the stopped receipt.
    with pytest.raises(PublicError) as stopped:
        example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    assert stopped.value.code=='tracking_review_invalid'
    assert len(example.calls)==1
    assert example.records.data['tracking_plan']['version']==4
    assert_no_old_approval(example.records.data)


def test_interrupted_between_quality_cache_and_targeted_review_cannot_bypass_source_checks(example):
    example.outcome['value']='uncertain'
    example.records.crash_before_targeted_write=True
    with pytest.raises(RuntimeError,match='before targeted review persisted'):
        example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    saved=deepcopy(example.records.data)
    assert saved['status']=='preparing' and saved['tracking_plan']['version']==2
    # Quality output and its required review are one atomic write. A failure
    # leaves neither half behind; the completed comparison receipt survives.
    assert not saved.get('reviewed_mask_quality')
    assert not saved.get('targeted_mask_review')
    assert saved['mask_identity_recheck_state']['status']=='completed'
    # Retry must reopen the source check using the receipt, not trust the cache
    # as if the required targeted review had already been persisted.
    example.pipeline.rebuild_reviewed_guide(saved,lambda:90)
    assert example.records.data['status']=='tracking_review'
    assert example.records.data['tracking_plan']['version']==4
    assert len(example.calls)==1
    assert not example.cloud.compositions and not example.cloud.enqueued


@pytest.mark.parametrize('dismiss',[False,True])
def test_manual_supplement_resolution_preserves_parent_and_cannot_auto_generate(example,dismiss):
    example.outcome['value']='uncertain'
    review=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    candidate=review['targeted_mask_review']['checks'][0]
    section=review['tracking_plan']['segments'][0]
    choices=[dict(segment_id=section['id'],absent=dismiss,
                  anchor=None if dismiss else deepcopy(candidate['anchor']))]
    claimed={**deepcopy(review),'status':'preparing','tracking_choices':choices,
             'tracking_applied_revision':review['tracking_revision'],
             'tracking_resolution_pending':True,'generation_after_review_revision':review['tracking_revision']}
    example.records.data=deepcopy(claimed)
    restored=example.pipeline.resolve_saved_review(claimed)
    for field in ('tracking_plan','tracking_choices','tracking_assignments','normalized_targets','tracking_metadata'):
        assert restored[field]==review['targeted_mask_review']['base_'+field]
    assert restored['normalized_targets'][1]==example.job['normalized_targets'][1]
    assert restored['tracking_applied_revision']==2
    assert not restored['targeted_mask_review']['pending']
    assert restored['targeted_mask_review']['decisions'][0]['absent'] is dismiss
    assert restored['reviewed_masks_uri']=='original'
    assert len(restored['mask_supplemental_anchors'])==(0 if dismiss else 1)
    assert_no_old_approval(restored)
    # Rebuild can only compose a fresh preview, with no second comparison/SAM/API render.
    example.cloud.allow_compose=True
    example.pipeline.rebuild_reviewed_guide(restored,lambda:90)
    ready=example.records.data
    assert ready['status']=='ready' and ready['guide_uri']!='old-guide'
    assert len(example.cloud.compositions)==1 and not example.cloud.enqueued
    assert len(example.calls)==1 and not ready['targeted_mask_review']['pending']
    assert_no_old_approval(ready)
    checked=assert_other_person_unchanged(example,ready)
    for frame in range(3):
        assert np.array_equal(checked[frame]==1,example.maps[frame]==1)
    if not dismiss:
        assert all(np.array_equal(a,b) for a,b in zip(example.maps,checked))
    else:
        assert all(not np.any(a==1) for a in checked[3:])


@pytest.mark.parametrize('dismiss',[False,True])
def test_v4_authenticated_api_queues_preview_only_ignores_auto_render_and_deduplicates(api,example,dismiss):
    example.outcome['value']='uncertain'
    review=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    client,records,cloud,provider=api
    headers,owner=authenticated(api)
    identifier=ready_job(records,owner)
    path=f'jobs/{identifier}.json'
    records.data[path].update({k:deepcopy(v) for k,v in review.items() if k!='id'})
    job=records.data[path]
    section=job['tracking_plan']['segments'][0]
    candidate=job['targeted_mask_review']['checks'][0]
    payload=dict(revision=job['tracking_revision'],choices=[dict(segment_id=section['id'],
        absent=dismiss,anchor=None if dismiss else candidate['anchor'])],
        generate_after_review=True,recover_missing_masks=True,generate_after_recovery=True)
    result=client.post(f'/api/jobs/{identifier}/tracking-review',headers=headers,json=payload)
    assert result.status_code==202,result.text
    saved=records.data[path]
    assert saved['status']=='queued' and saved['tracking_resolution_pending']
    assert saved['preparation_kind']=='tracking_rebuild'
    assert not saved['tracking_recovery_requested'] and not saved['recover_missing_masks']
    assert saved['generation_after_review_revision'] is None
    assert not saved['review_generation_queued'] and not provider.submissions
    assert cloud.enqueued==[('prepare',identifier)]
    assert saved['targeted_mask_review']['base_tracking_assignments']==example.job['tracking_assignments']
    assert saved['mask_quality_approved_sha256'] is None
    assert saved['mask_quality_approved_revision'] is None
    again=client.post(f'/api/jobs/{identifier}/tracking-review',headers=headers,json=payload)
    assert again.status_code==202,again.text
    assert cloud.enqueued==[('prepare',identifier)] and not provider.submissions
    assert len(example.calls)==1


def test_dismissing_one_uncertain_branch_preserves_already_confirmed_supplement(example,monkeypatch):
    for labels in example.maps[3:]:
        labels[1:9,1:11]=1  # A separate80-pixel branch, never part of baseline.
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w') as zipped:
        for frame,labels in enumerate(example.maps):
            _write(zipped,f'labels/labels_{frame:05d}.png',_png(labels))
    example.cloud.objects['original']=buffer.getvalue()
    def mixed(video,masks,metadata,candidates,**kwargs):
        example.calls.append(deepcopy(candidates))
        assert len(candidates)==2 and candidates[0]['pixels']>candidates[1]['pixels']
        return dict(requests=2,evidence=[{**deepcopy(c),'outcome':'confirmed' if i==0 else 'uncertain'}
            for i,c in enumerate(candidates)],usage=dict(input_tokens=20,estimated_cost_usd=.00002))
    monkeypatch.setattr('swapmein.mask_identity_rechecks.recheck_mask_identities',mixed)
    review=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    assert len(review['mask_supplemental_anchors'])==1
    affirmative_id=review['mask_supplemental_anchors'][0]['candidate_id']
    checks=review['targeted_mask_review']['checks']
    assert len(checks)==1 and checks[0]['candidate_id']!=affirmative_id
    before=archive_arrays(example.cloud.objects[review['reviewed_masks_uri']])
    section=review['tracking_plan']['segments'][0]
    claimed={**deepcopy(review),'status':'preparing','tracking_applied_revision':review['tracking_revision'],
        'tracking_choices':[dict(segment_id=section['id'],absent=True,anchor=None)]}
    example.records.data=deepcopy(claimed)
    restored=example.pipeline.resolve_saved_review(claimed)
    assert [c['candidate_id'] for c in restored['mask_supplemental_anchors']]==[affirmative_id]
    result=example.pipeline.check_reviewed_mask_quality(restored)
    after=assert_other_person_unchanged(example,result)
    assert all(np.array_equal(a,b) for a,b in zip(before,after))
    assert len(example.calls)==1
    assert not result['targeted_mask_review']['pending']
    assert_no_old_approval(result)


def test_losing_v4_review_claim_cannot_release_winners_preparation_lease(api,example,monkeypatch):
    example.outcome['value']='uncertain'
    review=example.pipeline.check_reviewed_mask_quality(deepcopy(example.job))
    client,records,cloud,provider=api
    headers,owner=authenticated(api)
    identifier=ready_job(records,owner);path=f'jobs/{identifier}.json'
    records.data[path].update({k:deepcopy(v) for k,v in review.items() if k!='id'})
    section=records.data[path]['tracking_plan']['segments'][0]
    candidate=records.data[path]['targeted_mask_review']['checks'][0]
    update=records.update; raced=False
    def competing_claim(record_path,mutation):
        nonlocal raced
        if record_path==path and not raced:
            raced=True
            records.data[path].update(status='queued',tracking_applied_revision=review['tracking_revision'],
                tracking_task_token='winning-preview-task')
            records.data['control/preparation.json']={'active':{'job_id':identifier,'until':time.time()+1800}}
        return update(record_path,mutation)
    monkeypatch.setattr(records,'update',competing_claim)
    result=client.post(f'/api/jobs/{identifier}/tracking-review',headers=headers,json={
        'revision':review['tracking_revision'],'choices':[dict(segment_id=section['id'],absent=False,anchor=candidate['anchor'])]})
    assert result.status_code==409 and raced,result.text
    assert records.data[path]['tracking_task_token']=='winning-preview-task'
    assert identifier in preparation_leases(records.data.get('control/preparation.json'))
    assert not cloud.enqueued and not provider.submissions


@pytest.fixture(autouse=True)
def legacy_continuity_regression_policy(monkeypatch):
    # Preserve explicit regression coverage for the paused implementation.
    # Production's default-disabled path is covered in test_mask_retention.py.
    monkeypatch.setattr('swapmein.mask_quality.CONTINUITY_CLEANUP_ENABLED', True)
