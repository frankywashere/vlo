"""First automatic guides share the reviewed-mask identity/approval contract."""
from copy import deepcopy
from io import BytesIO
import zipfile

import numpy as np
import pytest

from swapmein.mask_quality import _png, _write
from swapmein.scene_selection import mask_quality_review_pending
from test_mask_recheck_runtime import example, assert_no_old_approval, assert_other_person_unchanged  # noqa: F401


def initial_job(example,model):
    job={k:deepcopy(v) for k,v in example.job.items() if not k.startswith('tracking_')
         and k not in {'normalized_targets','reviewed_masks_uri'}}
    job.update(generation_model=model,auto_generate=True,google_fallback_approved=True)
    example.records.data=deepcopy(job)
    return job


def replace_original(example):
    buffer=BytesIO()
    with zipfile.ZipFile(buffer,'w') as archive:
        for frame,labels in enumerate(example.maps):
            _write(archive,f'labels/labels_{frame:05d}.png',_png(labels))
    example.cloud.objects['original']=buffer.getvalue()


@pytest.mark.parametrize('model',['minimax-h3','kling-4-0'])
@pytest.mark.parametrize('outcome',['confirmed','uncertain'])
def test_initial_automatic_masks_require_same_rechecks_and_approval_as_manual(example,model,outcome):
    job=initial_job(example,model)
    example.outcome['value']=outcome
    metadata=deepcopy(example.job['tracking_metadata'])
    targets=deepcopy(example.job['normalized_targets'])
    result=example.pipeline.check_automatic_mask_quality(job,metadata,targets,
        'original','prepared-vocals','prepared-depth',lambda:90)
    assert len(example.calls)==1
    assert result['tracking_applied_revision']==result['tracking_revision']-(1 if outcome=='uncertain' else 0)==1
    assert result['tracking_masks_uri']=='original'
    assert result['tracking_vocals_uri']=='prepared-vocals' and result['tracking_depth_uri']=='prepared-depth'
    assert result['mask_recheck_input_uri']=='original'
    assert result['reviewed_mask_quality']['identity_rechecks']['requests']==1
    assert mask_quality_review_pending(result)
    assert_no_old_approval(result)
    assert not example.cloud.compositions and not example.cloud.enqueued
    output=assert_other_person_unchanged(example,result)
    if outcome=='uncertain':
        assert result['status']=='tracking_review' and result['tracking_plan']['version']==4
        base=result['targeted_mask_review']
        assert [t['id'] for t in base['base_tracking_plan']['targets']]==['one','two']
        assert [a['selected_label'] for a in base['base_tracking_assignments']]==[1,2]
        assert [a['reference_index'] for a in base['base_tracking_assignments']]==[0,1]
        assert [c['anchor'] for c in base['base_tracking_choices']]==[t['anchor'] for t in targets]
        assert all(not np.any(frame==1) for frame in output[3:])
    else:
        assert result['status']=='preparing' and result['tracking_plan']['version']==2
        assert all(np.array_equal(a,b) for a,b in zip(example.maps,output))
        assert [a['selected_label'] for a in result['tracking_assignments']]==[1,2]


@pytest.mark.parametrize('model',['minimax-h3','kling-4-0'])
def test_clean_automatic_masks_do_not_create_requests_or_unnecessary_review(example,model,monkeypatch):
    for i in range(1,len(example.maps)):
        example.maps[i]=example.maps[0].copy()
    replace_original(example)
    monkeypatch.setattr('swapmein.cloudflare_credentials.server_cloudflare_credentials',
        lambda:pytest.fail('Clean masks need no identity-recheck credentials'))
    result=example.pipeline.check_automatic_mask_quality(initial_job(example,model),
        deepcopy(example.job['tracking_metadata']),deepcopy(example.job['normalized_targets']),
        'original','vocals',None,lambda:90)
    assert result['status']=='preparing' and not result.get('targeted_mask_review')
    assert not example.calls and not example.cloud.compositions and not example.cloud.enqueued
    assert result['reviewed_masks_uri']=='original'
    assert not result['reviewed_mask_quality']['review_required']
    assert not mask_quality_review_pending(result)
    # Automatic guides now retain a canonical quality plan too; its public
    # review metadata must remain usable even when no user check was needed.
    for section in result['tracking_plan']['segments']:
        assert section['start'] <= section['seek_time'] < section['end']
    import swapmein.app as app_module
    example.cloud.signed_url = lambda uri: 'https://example.test/' + uri
    monkeypatch.setattr(app_module, 'services', lambda: (example.records, example.cloud, None))
    public = app_module.view(result)
    assert len(public['tracking_segments']) == 2


@pytest.mark.parametrize('model',['minimax-h3','kling-4-0'])
def test_automatic_plan_keeps_per_scene_anchors_absence_and_stable_labels(example,model):
    targets=deepcopy(example.job['normalized_targets'])
    second=dict(x=48.5/96,y=30.5/64,time=3/24)
    targets[0]['scenes']=[dict(start=0,end=3/24,absent=False,anchor=targets[0]['anchor']),
                          dict(start=3/24,end=6/24,absent=False,anchor=second)]
    targets[1]['scenes']=[dict(start=0,end=3/24,absent=False,anchor=targets[1]['anchor']),
                          dict(start=3/24,end=6/24,absent=True,anchor=None)]
    for labels in example.maps[3:]:labels[labels==2]=0
    replace_original(example)
    metadata={**deepcopy(example.job['tracking_metadata']),'scene_cuts':[3/24]}
    job=initial_job(example,model);job['tracking_revision']=4;example.records.data=deepcopy(job)
    result=example.pipeline.check_automatic_mask_quality(job,metadata,targets,'original','vocals',None,lambda:90)
    assert not example.calls and not mask_quality_review_pending(result)
    assert result['tracking_revision']==result['tracking_applied_revision']==4
    assert result['tracking_metadata']['scene_cuts']==[3/24]
    assert result['normalized_targets']==targets
    assignments=result['tracking_assignments'];choices=result['tracking_choices']
    assert [(a['target_id'],a['start_frame'],a['end_frame'],a['selected_label'],a['absent']) for a in assignments]==[
        ('one',0,3,1,False),('one',3,6,1,False),('two',0,3,2,False),('two',3,6,2,True)]
    assert choices[1]['anchor']==second
    assert choices[3]['absent'] and choices[3]['anchor'] is None
    assert_other_person_unchanged(example,result)


@pytest.fixture(autouse=True)
def legacy_continuity_regression_policy(monkeypatch):
    # Preserve explicit regression coverage for the paused implementation.
    # Production's default-disabled path is covered in test_mask_retention.py.
    monkeypatch.setattr('swapmein.mask_quality.CONTINUITY_CLEANUP_ENABLED', True)
