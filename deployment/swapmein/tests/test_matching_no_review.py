"""The opt-in automatic fallback uses the existing Easy Mode guide and prompt."""
import pytest

from swapmein.errors import PublicError
from swapmein.pipeline import Pipeline
from swapmein.provider import generation_payload
from test_pipeline_v2 import harness,media,image_at


@pytest.mark.parametrize('provider',['workers','replicate'])
@pytest.mark.parametrize('enhanced,depth,mesh',[(True,False,False),(True,True,True),(False,False,False),(False,False,True)])
@pytest.mark.parametrize('selection',['single','multi'])
def test_matching_failure_continues_with_exact_option_five_route(monkeypatch,media,capsys,provider,enhanced,depth,mesh,selection):
    def fail(*args,**kwargs):raise PublicError('untrusted provider detail',503,code='cloudflare_matching_unavailable')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',fail)
    kwargs=dict(easy=True,enhanced=enhanced,selection=selection,audio_processing=False,
        preparation_provider=provider,depth_guidance=depth,face_mesh=mesh,guide_style_version=2)
    baseline,base_cloud,*_=harness(monkeypatch,media,prompt_variant='identity_tint_tracking_v1',**kwargs)
    job,cloud,*_=harness(monkeypatch,media,prompt_variant='cloudflare_identity_v1',matching_no_review=True,**kwargs)
    assert baseline['status']==job['status']=='ready',job.get('message')
    assert job['requested_prompt_variant']=='cloudflare_identity_v1'
    assert job['prompt_variant']=='identity_tint_tracking_v1' and job['matching_fallback_used']
    assert job['matching_no_review'] is True and not job['identity_matching_completed']
    assert job['mask_style']==baseline['mask_style']
    assert not job.get('tracking_plan') and not job.get('tracking_reviewed')
    assert ('generate',job['id']) in cloud.enqueued
    comp=[p for _,ep,p in cloud.calls if ep=='/compose'];base_comp=[p for _,ep,p in base_cloud.calls if ep=='/compose']
    if not enhanced and not mesh:
        assert not comp and not base_comp
    else:
        assert comp[-1]['fill_mode']==base_comp[-1]['fill_mode']
        assert comp[-1].get('target_styles')==base_comp[-1].get('target_styles')
        # Compare actual categorical mask pixels, not just settings metadata.
        assert (image_at(cloud.objects[comp[-1]['masks_uri']])==image_at(base_cloud.objects[base_comp[-1]['masks_uri']])).all()
    imgs=['one','two'] if selection=='multi' else ['one']
    def payload(saved):return generation_payload(4,imgs,'guide',enhanced,anchor=saved['normalized_anchor'],
        mask_style=saved['mask_style'],targets=saved.get('normalized_targets') or None,
        audio_processing=False,face_mesh=mesh,prompt_variant=saved['prompt_variant'],easy_mode=True)
    assert payload(job)==payload(baseline)
    logs=capsys.readouterr().out
    assert 'cloudflare_matching_fallback' in logs
    assert 'untrusted provider detail' not in logs


@pytest.mark.parametrize('easy,selection',[(False,'multi'),(True,'all')])
def test_no_review_setting_never_changes_manual_or_replace_everyone(monkeypatch,media,easy,selection):
    def forbidden(*args,**kwargs):raise AssertionError('No matching on this route')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',forbidden)
    job,*_=harness(monkeypatch,media,easy=easy,enhanced=True,selection=selection,audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert job['status']==('ready' if easy else 'tracking_review')
    assert job['prompt_variant']=='cloudflare_identity_v1' and not job.get('matching_fallback_used')


def test_option_five_failure_is_not_turned_into_unapproved_generation(monkeypatch,media):
    def fail(*args,**kwargs):raise PublicError('Matching unavailable',503,code='cloudflare_matching_unavailable')
    def fallback_fail(*args,**kwargs):raise PublicError('Preparation cannot continue',code='tracking_target_conflict')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',fail)
    monkeypatch.setattr(Pipeline,'prepare_easy_selection',fallback_fail)
    job,cloud,*_=harness(monkeypatch,media,easy=True,enhanced=True,selection='multi',audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert job['status']=='failed' and not any(kind=='generate' for kind,_ in cloud.enqueued)


@pytest.mark.parametrize('code',['tracking_invalid','tracking_target_conflict','worker_failed'])
def test_invalid_processing_is_not_hidden_by_no_review(monkeypatch,media,code):
    def fail(*args,**kwargs):raise PublicError('Invalid processing',503,code=code)
    def forbidden(*args,**kwargs):raise AssertionError('Must not use fallback for invalid processing')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',fail)
    monkeypatch.setattr(Pipeline,'prepare_easy_selection',forbidden)
    job,cloud,*_=harness(monkeypatch,media,easy=True,enhanced=True,selection='multi',audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert job['status']=='tracking_review' and not job.get('matching_fallback_used')
    assert not any(kind=='generate' for kind,_ in cloud.enqueued)


def test_fallback_does_not_run_a_second_initial_sam_retry(monkeypatch,media):
    def failed_recovery(pipeline,job,*args,**kwargs):
        pipeline.update_job(job['id'],identity_anchor_recovery_attempted=True,retry_missing_masks_with_sam=True,
            identity_anchor_recovery_report=[{'outcome':'coordinate_fallback'}])
        raise PublicError('Unrecovered',code='cloudflare_anchor_recovery')
    calls=[]
    def existing_easy_recovery(pipeline,job,masks,metadata,targets,*args):
        calls.append(job['retry_missing_masks_with_sam'])
        assert job['retry_missing_masks_with_sam'] is False
        return None
    monkeypatch.setattr('swapmein.cloudflare_recovery.recover_identity_anchors',failed_recovery)
    monkeypatch.setattr('swapmein.initial_mask_recovery.recover_initial_selection',existing_easy_recovery)
    job,cloud,*_=harness(monkeypatch,media,easy=True,enhanced=True,selection='multi',audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1',matching_no_review=True)
    assert calls==[False]
    assert job['status']=='ready' and job['matching_fallback_used']
    assert job['identity_anchor_recovery_report']==[{'outcome':'coordinate_fallback'}]
    assert job['retry_missing_masks_with_sam'] is True


def test_missing_cloudflare_credentials_has_recoverable_matching_code(monkeypatch):
    monkeypatch.setattr('swapmein.cloudflare_credentials.server_cloudflare_credentials',
        lambda:(_ for _ in ()).throw(PublicError('Secret unavailable',503)))
    with pytest.raises(PublicError) as error:
        Pipeline(None,None,None).match_cloudflare_identities({'id':'test'},None,None,None,None,None,lambda:60)
    assert error.value.code=='cloudflare_matching_unavailable'
