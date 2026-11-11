"""A suspect mask opens a usable review and never spends on re-selection."""
from copy import deepcopy

import swapmein.app as gateway
from swapmein.mask_quality_recovery import identity_recovery_fields
from swapmein.scene_selection import analyze
from test_api import api, authenticated, ready_job


def recovery_job(api):
    client, records, cloud, provider = api
    headers, owner = authenticated(api)
    identifier = ready_job(records, owner)
    job = records.data[f'jobs/{identifier}.json']
    target = {'id':'selected','name':'Person 1','reference_index':0,
              'anchor':{'x':.2,'y':.3,'time':0}, 'guide_anchor_untrusted':True,
              'scenes':[{'start':0,'end':5,'absent':False,'anchor':{'x':.2,'y':.3,'time':0}}]}
    metadata = {'width':32,'height':24,'duration':5,'frames':120,
                'anchor':{**target['anchor'],'frame':0},'scene_cuts':[2]}
    job.update(normalized_targets=[target],tracking_metadata=metadata,
        normalized_uri='gs://private-app/normalized.mp4', tracking_applied_revision=1,
        tracking_plan=analyze(metadata,[target]), mask_style='cyan_overlay_20',
        reviewed_mask_quality={'sha256':'clean-guide','review_required':True,
            'suppressed_ranges':[{'target_id':'selected','start_frame':0,'end_frame':120,'reason':'identity_reselection'}]})
    job.update(identity_recovery_fields(job))
    return client,records,cloud,provider,headers,identifier


def test_reselection_uses_original_preview_and_requires_guide_approval_before_one_submit(api):
    client, records, cloud, provider, headers, identifier = recovery_job(api)
    path=f'/api/jobs/{identifier}'
    reopened=client.get(path,headers=headers)
    assert reopened.status_code==200 and reopened.json()['status']=='tracking_review'
    assert 'normalized.mp4' in reopened.json()['source_preview_url']
    assert reopened.json().get('tracking_choices',[])==[]
    job=deepcopy(records.data[f'jobs/{identifier}.json'])
    choices=[{'segment_id':s['id'],'absent':False,
              'anchor':{'x':.7,'y':.5,'time':s['seek_time']}} for s in job['tracking_plan']['segments']]
    response=client.post(path+'/tracking-review',headers=headers,
        json={'revision':2,'choices':choices,'generate_after_review':True})
    assert response.status_code==202 and response.json()['status']=='ready',response.text
    assert not provider.submissions and not cloud.enqueued
    updated=records.data[f'jobs/{identifier}.json']
    assert updated['normalized_targets'][0]['anchor']['x']==.7
    assert not updated['normalized_targets'][0].get('guide_anchor_untrusted')
    assert updated['generation_after_review_revision'] is None
    assert not updated['identity_recovery']
    assert updated['normalized_targets'][0]['scenes'][0]['guide_mask_suppressed_ranges']
    for _ in range(2):
        accepted=client.post(path+'/generate',headers=headers,json={'approved':True})
        assert accepted.status_code==200,accepted.text
    assert len(provider.submissions)==1
    assert records.data[f'jobs/{identifier}.json']['mask_quality_approved_sha256']=='clean-guide'


def test_stale_revision_cannot_replace_recovered_identity(api):
    client, records, cloud, provider, headers, identifier = recovery_job(api)
    original=deepcopy(records.data[f'jobs/{identifier}.json'])
    choices=[{'segment_id':s['id'],'absent':False,
        'anchor':{'x':.7,'y':.5,'time':s['seek_time']}} for s in original['tracking_plan']['segments']]
    response=client.post(f'/api/jobs/{identifier}/tracking-review',headers=headers,
        json={'revision':1,'choices':choices,'generate_after_review':True})
    assert response.status_code==400
    assert records.data[f'jobs/{identifier}.json']==original
    assert not provider.submissions and not cloud.enqueued


def test_rebuilt_unsafe_guide_never_publishes_ready_or_queues_generation(api, monkeypatch):
    from contextlib import contextmanager
    from swapmein.pipeline import Pipeline
    import swapmein.pipeline as pipeline_module
    client, records, cloud, provider, headers, identifier = recovery_job(api)
    job=deepcopy(records.data[f'jobs/{identifier}.json'])
    job.update(status='preparing', reviewed_masks_uri='gs://private-app/checked.zip',
               reviewed_coverage=[0]*120, tracking_vocals_uri='gs://private-app/audio.wav',
               generation_after_review_revision=1, tracking_reviewed=True)
    records.data[f'jobs/{identifier}.json']=deepcopy(job)
    statuses=[]
    original_update=records.update
    def tracked_update(path,mutation):
        result=original_update(path,mutation)
        if path==f'jobs/{identifier}.json':statuses.append(result['status'])
        return result
    monkeypatch.setattr(records,'update',tracked_update)
    @contextmanager
    def worker(_kind):
        yield lambda endpoint,payload,remaining: {'result':'composed'}
    monkeypatch.setattr(cloud,'worker',worker,raising=False)
    monkeypatch.setattr(cloud,'worker_output',lambda *_:'gs://private-app/composed.mp4',raising=False)
    monkeypatch.setattr(cloud,'copy',lambda *_:'gs://private-app/clean-guide.mp4',raising=False)
    monkeypatch.setattr(pipeline_module,'guide_style',lambda *args,**kwargs:('overlay','cyan_overlay_20'))
    monkeypatch.setattr(Pipeline,'check_reviewed_mask_quality',lambda self,current:current)
    Pipeline(gateway.settings,records,cloud).rebuild_reviewed_guide(job,lambda:60)
    assert statuses and statuses[-1]=='tracking_review' and 'ready' not in statuses
    assert records.data[f'jobs/{identifier}.json']['guide_uri']=='gs://private-app/clean-guide.mp4'
    assert not provider.submissions and not cloud.enqueued
