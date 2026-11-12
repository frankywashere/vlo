"""Legacy no-review preference never weakens failed identity matching."""
import numpy as np
import pytest

from swapmein.errors import PublicError
from swapmein.pipeline import Pipeline
from test_pipeline_v2 import assert_no_tracking_preview,harness,media,image_at


@pytest.mark.parametrize('provider',['workers','replicate'])
@pytest.mark.parametrize('model,enhanced,depth,mesh',[
    ('minimax-h3',True,False,False),('minimax-h3',True,True,True),
    ('minimax-h3',False,False,False),('minimax-h3',False,False,True),
    ('kling-4-0',True,False,False),('kling-4-0',False,False,False)])
@pytest.mark.parametrize('selection',['single','multi'])
def test_matching_failure_keeps_original_route_and_requires_scene_review(monkeypatch,media,capsys,provider,enhanced,depth,mesh,selection,model):
    seen=[]
    def fail(*args,**kwargs):
        seen.append(True)
        raise PublicError('untrusted provider detail',503,code='cloudflare_matching_unavailable')
    def forbidden(*args,**kwargs):raise AssertionError('Do not degrade to legacy Easy selection')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',fail)
    monkeypatch.setattr(Pipeline,'prepare_easy_selection',forbidden)
    kwargs=dict(easy=True,enhanced=enhanced,selection=selection,audio_processing=False,
        preparation_provider=provider,depth_guidance=depth,face_mesh=mesh,guide_style_version=2,
        generation_model=model,generation_resolution='720p' if model=='kling-4-0' else '768P')
    job,cloud,_,previews,_=harness(monkeypatch,media,prompt_variant='cloudflare_identity_v1',matching_no_review=True,**kwargs)
    assert job['status']=='tracking_review',job.get('message')
    assert seen==[True] and previews and job['tracking_uri'] in cloud.objects
    assert job['prompt_variant']=='cloudflare_identity_v1' and not job.get('matching_fallback_used')
    assert job['matching_no_review'] is True and not job['identity_matching_completed']
    assert job['identity_matching_error']=='cloudflare_matching_unavailable'
    assert job['identity_matching_recheck_blocked'] is True
    assert job['tracking_plan']['review_required'] and not job.get('tracking_reviewed')
    assert job['tracking_masks_uri'] and job['normalized_uri']
    assert {7,40000} <= set(np.unique(image_at(cloud.objects[job['tracking_masks_uri']])))
    assert [target['reference_index'] for target in job['normalized_targets']]==list(range(2 if selection=='multi' else 1))
    assert all(target['anchor']['x']==(.75 if target['id']=='person-2' else .25) for target in job['normalized_targets'])
    assert not any(kind=='generate' for kind,_ in cloud.enqueued)
    assert not any(endpoint=='/compose' for _,endpoint,_ in cloud.calls)
    assert len(cloud.replicate_calls)==(1 if provider=='replicate' else 0)
    assert len([True for kind,_,payload in cloud.calls
                if kind=='gpu' and payload.get('operation') in {'sam','both'}])==(1 if provider=='workers' else 0)
    logs=capsys.readouterr().out
    assert 'cloudflare_matching_review_required' in logs
    assert 'cloudflare_matching_fallback' not in logs
    assert 'untrusted provider detail' not in logs


@pytest.mark.parametrize('easy,selection',[(False,'multi'),(True,'all')])
def test_no_review_setting_never_changes_manual_or_replace_everyone(monkeypatch,media,easy,selection):
    def forbidden(*args,**kwargs):raise AssertionError('No matching on this route')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',forbidden)
    job,cloud,_,previews,_=harness(monkeypatch,media,easy=easy,enhanced=True,selection=selection,audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert job['status']==('ready' if easy else 'tracking_review')
    if easy:assert_no_tracking_preview(job,cloud,previews)
    else:assert previews and job['tracking_uri'] in cloud.objects
    assert job['prompt_variant']=='cloudflare_identity_v1' and not job.get('matching_fallback_used')


def test_failed_service_never_enters_option_five_or_unapproved_generation(monkeypatch,media):
    def fail(*args,**kwargs):raise PublicError('Matching unavailable',503,code='cloudflare_matching_unavailable')
    def fallback_fail(*args,**kwargs):raise AssertionError('Legacy selection must never be called')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',fail)
    monkeypatch.setattr(Pipeline,'prepare_easy_selection',fallback_fail)
    job,cloud,*_=harness(monkeypatch,media,easy=True,enhanced=True,selection='multi',audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert job['status']=='tracking_review' and not any(kind=='generate' for kind,_ in cloud.enqueued)


@pytest.mark.parametrize('code',['tracking_invalid','tracking_target_conflict','worker_failed','cloudflare_identity_uncertain'])
def test_invalid_processing_is_not_hidden_by_no_review(monkeypatch,media,code):
    def fail(*args,**kwargs):raise PublicError('Invalid processing',503,code=code)
    def forbidden(*args,**kwargs):raise AssertionError('Must not use fallback for invalid processing')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',fail)
    monkeypatch.setattr(Pipeline,'prepare_easy_selection',forbidden)
    job,cloud,_,previews,_=harness(monkeypatch,media,easy=True,enhanced=True,selection='multi',audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert job['status']=='tracking_review' and not job.get('matching_fallback_used')
    assert previews and job['tracking_uri'] in cloud.objects
    assert job['scene_mask_thumbnail_uris']
    assert not any(kind=='generate' for kind,_ in cloud.enqueued)


def test_failed_anchor_repair_keeps_saved_masks_and_never_repeats_initial_sam(monkeypatch,media):
    def failed_recovery(pipeline,job,*args,**kwargs):
        pipeline.update_job(job['id'],identity_anchor_recovery_attempted=True,retry_missing_masks_with_sam=True,
            identity_anchor_recovery_report=[{'outcome':'coordinate_fallback'}])
        raise PublicError('Unrecovered',code='cloudflare_anchor_recovery')
    def existing_easy_recovery(*args):raise AssertionError('Do not retry SAM after failed anchor recovery')
    def no_matcher(*args):raise AssertionError('Failed anchor repair cannot reach identity matching')
    monkeypatch.setattr('swapmein.cloudflare_recovery.recover_identity_anchors',failed_recovery)
    monkeypatch.setattr('swapmein.initial_mask_recovery.recover_initial_selection',existing_easy_recovery)
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',no_matcher)
    job,cloud,*_=harness(monkeypatch,media,easy=True,enhanced=True,selection='multi',audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert job['status']=='tracking_review' and not job.get('matching_fallback_used')
    assert job['prompt_variant']=='cloudflare_identity_v1'
    assert job['identity_anchor_recovery_report']==[{'outcome':'coordinate_fallback'}]
    assert job['retry_missing_masks_with_sam'] is True
    assert job['identity_matching_error']=='cloudflare_anchor_recovery'
    assert not any(kind=='generate' for kind,_ in cloud.enqueued)


def test_missing_cloudflare_credentials_has_recoverable_matching_code(monkeypatch):
    monkeypatch.setattr('swapmein.cloudflare_credentials.server_cloudflare_credentials',
        lambda:(_ for _ in ()).throw(PublicError('Secret unavailable',503)))
    with pytest.raises(PublicError) as error:
        Pipeline(None,None,None).match_cloudflare_identities({'id':'test'},None,None,None,None,None,lambda:60)
    assert error.value.code=='cloudflare_matching_unavailable'
