"""Option 6 changes Easy routing without changing existing prompt choices."""
import copy
from pathlib import Path

import pytest

from swapmein.cloudflare_budget import MatchingBudget, PATH, LIMIT_MICRO_USD, RESERVE_MICRO_USD
from swapmein.errors import PublicError
from swapmein.multi_tracking import build_selected_masks, selected_tracks
from swapmein.pipeline import Pipeline
from swapmein.provider import generation_payload
from test_pipeline_v2 import assert_no_tracking_preview, harness, media


@pytest.mark.parametrize("provider", ["workers", "replicate"])
@pytest.mark.parametrize("enhanced,depth,mesh", [(True,False,False),(True,False,True),(True,True,True),(False,False,False),(False,False,True)])
@pytest.mark.parametrize("selection", ["single", "multi"])
def test_easy_matching_assignments_reach_guide_and_manual_prompt(monkeypatch, media, provider, enhanced, depth, mesh, selection):
    seen=[]
    def match(self,job,video,masks,metadata,targets,root,remaining):
        assert video.read_bytes()==media['working'].read_bytes()  # never depth input
        assert remaining()>0
        seen.append(True)
        stable=root/'matched.zip'
        tracks=selected_tracks(masks,metadata,targets)
        coverage=build_selected_masks(masks,metadata,tracks,stable)
        targets=[{**t,'scenes':[{'start':0,'end':2,'absent':False,'anchor':t['anchor']},
                               {'start':2,'end':4,'absent':False,'anchor':{**t['anchor'],'time':3}}]} for t in targets]
        return dict(masks_path=stable,coverage=coverage,targets=targets,assignments=[],diagnostics={'calls':1})
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',match)
    job,cloud,_,previews,_=harness(monkeypatch,media,easy=True,enhanced=enhanced,selection=selection,
        audio_processing=False,preparation_provider=provider,depth_guidance=depth,face_mesh=mesh,
        prompt_variant='cloudflare_identity_v1',guide_style_version=2)
    assert seen and job['status']=='ready',job.get('message')
    assert_no_tracking_preview(job,cloud,previews)
    assert cloud.enqueued==[('generate',job['id'])]
    assert job['identity_matching_completed']
    assert job['normalized_targets'][0]['scenes'][1]['anchor']['time']==3
    compose=[p for _,ep,p in cloud.calls if ep=='/compose'][-1]
    assert compose['fill_mode']==('original' if not enhanced else 'depth' if depth else 'translucent')
    if enhanced and not depth:
        assert job['mask_style'].startswith('identity_overlay')
        if selection=='multi':assert compose['target_styles'][1]['color']=='#ff4fd8'
    if not enhanced:assert job['mask_style']=='none'
    images=['ref1','ref2'] if selection=='multi' else ['ref1']
    kwargs=dict(enhanced_masking=enhanced,targets=job['normalized_targets'],mask_style=job['mask_style'],face_mesh=mesh,audio_processing=False)
    assert generation_payload(4,images,'guide',prompt_variant='cloudflare_identity_v1',easy_mode=True,**kwargs)==generation_payload(4,images,'guide',prompt_variant='identity_tint_tracking_v1',easy_mode=False,**kwargs)


@pytest.mark.parametrize('provider',['workers','replicate'])
def test_matcher_failure_preserves_inputs_and_routes_manual_scene_review(monkeypatch,media,provider):
    def fail(*args,**kwargs):raise PublicError('Matching unavailable',503,code='cloudflare_matching_unavailable')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',fail)
    job,cloud,_,previews,_=harness(monkeypatch,media,easy=True,enhanced=True,selection='multi',audio_processing=False,
        preparation_provider=provider,depth_guidance=False,prompt_variant='cloudflare_identity_v1')
    assert job['status']=='tracking_review'
    assert not job['identity_matching_completed']
    assert job['tracking_masks_uri'] and job['normalized_uri']
    assert previews and job['tracking_uri'] in cloud.objects
    assert job['scene_mask_thumbnail_uris']
    assert job['tracking_plan']['review_required']
    assert not any(kind=='generate' for kind,_ in cloud.enqueued)
    # The same Easy job can complete the existing manual-review rebuild.
    from dataclasses import replace
    from swapmein.config import Settings
    choices=[{'segment_id':section['id'],'absent':False,'anchor':{
        'x':.75 if section['target_id']=='person-2' else .25,'y':.5,'time':section['seek_time']}}
        for section in job['tracking_plan']['segments'] if section['needs_review']]
    job.update(tracking_choices=choices,tracking_resolution_pending=True,tracking_applied_revision=1,
        generation_after_review_revision=1,tracking_recovery_requested=True,tracking_task_token='saved-review-task')
    def enqueue(kind,job_id,task_token=None):
        assert task_token=='saved-review-task'
        cloud.enqueued.append((kind,job_id))
    cloud.enqueue=enqueue
    class Saved:
        data=copy.deepcopy(job)
        def update(self,path,fn):self.data=fn(copy.deepcopy(self.data));return copy.deepcopy(self.data)
    saved=Saved()
    Pipeline(replace(Settings(),app_bucket='app',worker_bucket='workers'),saved,cloud).rebuild_reviewed_guide(job,lambda:60)
    assert saved.data['id']==job['id'] and saved.data['easy_mode'] is True
    assert saved.data['status']=='ready' and saved.data['tracking_reviewed']
    assert saved.data['review_generation_queued']
    assert all(t['scenes'] for t in saved.data['normalized_targets'])
    assert any(kind=='generate' for kind,_ in cloud.enqueued)


@pytest.mark.parametrize('easy,selection',[(False,'multi'),(True,'all')])
def test_manual_and_replace_everyone_skip_cloudflare(monkeypatch,media,easy,selection):
    def forbidden(*args,**kwargs):raise AssertionError('Cloudflare must not run here')
    monkeypatch.setattr(Pipeline,'match_cloudflare_identities',forbidden)
    job,cloud,_,previews,_=harness(monkeypatch,media,easy=easy,enhanced=True,selection=selection,audio_processing=False,
        depth_guidance=False,prompt_variant='cloudflare_identity_v1')
    assert job['status']==('ready' if easy else 'tracking_review')
    if easy:assert_no_tracking_preview(job,cloud,previews)
    else:assert previews and job['tracking_uri'] in cloud.objects


class Records:
    def __init__(self):self.data={}
    def update(self,path,fn):self.data[path]=fn(copy.deepcopy(self.data.get(path)));return copy.deepcopy(self.data[path])


def test_matching_budget_reconciles_usage_once_but_retains_unknown_reservations():
    records=Records();b=MatchingBudget(records,'job');attempt=b.reserve();before=records.data[PATH]['accounted_micro_usd']
    b.settle(attempt,{'estimated_cost_usd':.0001});after=records.data[PATH]['accounted_micro_usd']
    assert after==before-RESERVE_MICRO_USD+200
    b.settle(attempt,{'estimated_cost_usd':.0001});assert records.data[PATH]['accounted_micro_usd']==after
    unknown=b.reserve();b.settle(unknown,{'estimated_cost_usd':None});assert records.data[PATH]['accounted_micro_usd']==after+RESERVE_MICRO_USD
    records.data[PATH]['accounted_micro_usd']=LIMIT_MICRO_USD
    with pytest.raises(PublicError,match='spending limit'):b.reserve()

from test_api import api, authenticated, ready_job
from swapmein.pipeline_settings import save_pipeline_settings
from test_staff_permissions import setup_staff


@pytest.mark.parametrize('matched,reviewed,expected',[(False,False,409),(True,False,200),(False,True,200)])
def test_option_six_cannot_submit_without_matching_or_review(api,matched,reviewed,expected):
    client,records,cloud,provider=api
    headers,owner=authenticated(api)
    job_id=ready_job(records,owner)
    job=records.data[f'jobs/{job_id}.json']
    job.update(prompt_variant='cloudflare_identity_v1',identity_matching_completed=matched,tracking_reviewed=reviewed)
    job['setup']['easy_mode']=True
    response=client.post(f'/api/jobs/{job_id}/generate',headers=headers,json={'approved':True})
    assert response.status_code==expected,response.text
    assert len(provider.submissions)==(1 if expected==200 else 0)


def test_option_six_setting_requires_credentials_and_six_is_the_new_default(api,monkeypatch):
    client,records=setup_staff(api,monkeypatch,['pipeline_settings'])
    monkeypatch.setattr('swapmein.cloudflare_credentials.cloudflare_key_ready',lambda:False)
    assert client.post('/api/commerce/admin/pipeline',json={'revision':0,'prompt_variant':'cloudflare_identity_v1'}).status_code==409
    assert client.get('/api/commerce/admin').json()['pipeline']['prompt_variant']=='cloudflare_identity_v1'
    monkeypatch.setattr('swapmein.cloudflare_credentials.cloudflare_key_ready',lambda:True)
    response=client.post('/api/commerce/admin/pipeline',json={'revision':0,'prompt_variant':'cloudflare_identity_v1'})
    assert response.status_code==200,response.text
    assert response.json()['prompt_variant']=='cloudflare_identity_v1'


@pytest.mark.parametrize('style',['none','identity_overlay_multi_20','identity_depth_multi','shared_depth_multi'])
@pytest.mark.parametrize('audio,silent',[(False,False),(True,False),(False,True)])
@pytest.mark.parametrize('mesh',[False,True])
def test_option_six_matches_manual_prompt_for_all_saved_media_modes(style,audio,silent,mesh):
    from test_prompt_variants import targets_with_reviews
    kwargs=dict(enhanced_masking=style!='none',mask_style=style,targets=targets_with_reviews(),
                audio_processing=audio,submit_without_audio=silent,face_mesh=mesh)
    auto=generation_payload(6,['a','b'],'v',prompt_variant='cloudflare_identity_v1',easy_mode=True,**kwargs)
    manual=generation_payload(6,['a','b'],'v',prompt_variant='identity_tint_tracking_v1',easy_mode=False,**kwargs)
    assert auto==manual


@pytest.mark.parametrize('direct',[False,True])
def test_unmasked_easy_option_six_snapshots_internal_sam_provider(api,monkeypatch,direct):
    import json
    from io import BytesIO
    from PIL import Image
    from test_api import upload_options
    from swapmein.pipeline_settings import PATH as SETTINGS_PATH
    client,records,*_=api;headers,_=authenticated(api);seen=[]
    def snapshot(provider,enhanced_masking=True):
        seen.append((provider,enhanced_masking));return {'preparation_provider':provider,'replicate_credential_version':'frozen-version'}
    monkeypatch.setattr('swapmein.app.preparation_snapshot',snapshot)
    records.data[SETTINGS_PATH]={'preparation_provider':'replicate','prompt_variant':'cloudflare_identity_v1',
        'face_mesh_enabled':False,'face_mesh_masked_default':False,'face_mesh_unmasked_default':False}
    request=upload_options();request['setup'].update(easy_mode=True,enhanced_character_masking=False)
    if direct:
        ticket=client.post('/api/uploads',headers=headers,json=request)
        assert ticket.status_code==201,ticket.text
        response=client.post(f"/api/uploads/{ticket.json()['id']}/complete",headers=headers)
    else:
        im=BytesIO();Image.new('RGB',(256,256),'blue').save(im,'PNG')
        response=client.post('/api/jobs',headers=headers,files={'clip':('clip.mp4',b'source','video/mp4'),
            'reference':('ref.png',im.getvalue(),'image/png')},data={'setup':json.dumps(request['setup'])})
    assert response.status_code==202,response.text
    assert seen==[('replicate',True)]
