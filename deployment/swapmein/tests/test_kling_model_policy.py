"""Model access, pricing and immutable uploads; never submits paid inference."""
from io import BytesIO
import json
from PIL import Image
import pytest
import swapmein.app as gateway
from swapmein.commerce import (generation_credit_cost, generation_quote, default_catalog,
                              kling_credit_rate, public_catalog, save_catalog)
from swapmein.errors import PublicError
from swapmein.generation_models import KLING, MINIMAX, generation_snapshot
from swapmein.job_settings import settings_used
from swapmein.media import Setup
from swapmein.pipeline_settings import PATH, pipeline_settings, save_pipeline_settings
from swapmein.self_hosted_route import generation_route_snapshot
from test_api import api, authenticated
from test_creation_credit_gate import enable_credits, ACCOUNT_ID


def options(duration=15):
    return {'source_bytes':1024, 'reference_bytes':1024,
            'setup':{'generation_model':KLING, 'trim':[0,duration],
                     'anchor':{'x':.5,'y':.5,'time':1}, 'face_mesh':True,
                     'enhanced_character_masking':False}}


def test_hidden_model_cannot_bypass_flag_via_direct_api(api):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    response = client.post('/api/uploads', headers=headers, json=options())
    assert response.status_code == 409 and response.json()['code'] == 'kling_disabled'
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions


def test_toapis_cannot_take_kling_even_when_enabled(api):
    client, records, cloud, provider = api
    records.data[PATH] = {'kling4_enabled':True}
    headers, owner = authenticated(api)
    records.data['sessions/'+owner+'.json']['provider'] = 'ToAPIs'
    response = client.post('/api/uploads', headers=headers, json=options())
    assert response.status_code == 409 and response.json()['code'] == 'kling_provider_unavailable'
    assert not cloud.upload_sizes and not provider.submissions


def test_kling_locks_mesh_and_depth_and_bypasses_private_override(api):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    records.data[PATH] = {'kling4_enabled':True,'face_mesh_enabled':True,
                          'depth_guidance':True,'audio_processing':True}
    response = client.post('/api/uploads', headers=headers, json=options())
    assert response.status_code == 201
    manifest = records.data[f"uploads/{response.json()['id']}/manifest.json"]
    assert manifest['generation_model'] == KLING and manifest['face_mesh'] is False
    assert manifest['depth_guidance'] is False and manifest['audio_processing'] is False
    assert manifest['setup']['enhanced_character_masking'] is True
    assert manifest['restore_original_audio'] is True
    setup = {'generation_model':KLING,'targets':[{},{}]}
    assert generation_route_snapshot(setup,'google_h3')['multi_person_generation_route'] == 'current'
    setup['generation_model'] = MINIMAX
    assert generation_route_snapshot(setup,'google_h3')['multi_person_generation_route'] == 'google_h3'


def test_kling_fifteen_second_ticket_pins_replicate_and_survives_flag_change(api, monkeypatch):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    records.data[PATH] = {'kling4_enabled':True,'preparation_provider':'replicate'}
    monkeypatch.setenv('REPLICATE_API_TOKEN','')
    response = client.post('/api/uploads', headers=headers, json=options(15))
    assert response.status_code == 503
    assert not cloud.upload_sizes
    monkeypatch.setenv('REPLICATE_API_TOKEN','replicate-test-token')
    response = client.post('/api/uploads', headers=headers, json=options(15))
    assert response.status_code == 201
    jobid = response.json()['id']
    manifest = records.data[f'uploads/{jobid}/manifest.json']
    assert manifest['preparation_provider'] == 'replicate'
    records.data[PATH] = {'kling4_enabled':False,'preparation_provider':'replicate','depth_guidance':True}
    assert client.post(f'/api/uploads/{jobid}/complete', headers=headers).status_code == 202
    job = records.data[f'jobs/{jobid}.json']
    assert job['generation_model'] == KLING and job['preparation_provider'] == 'replicate'
    assert job['face_mesh'] is False and job['depth_guidance'] is False
    assert not provider.submissions


@pytest.mark.parametrize('enabled,silent',[(True,False),(False,False),(True,True)])
def test_kling_audio_choice_is_pinned_at_ticket_acceptance(api,monkeypatch,enabled,silent):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    monkeypatch.setenv('REPLICATE_API_TOKEN','replicate-test-token')
    records.data[PATH]={'kling4_enabled':True,'audio_processing':not enabled,'kling_audio_processing':enabled}
    body=options(15);body['setup']['submit_without_audio']=silent
    response=client.post('/api/uploads',headers=headers,json=body)
    assert response.status_code==201,response.text
    jobid=response.json()['id'];manifest=records.data[f'uploads/{jobid}/manifest.json']
    expected=enabled and not silent
    assert manifest['generation_snapshot']['audio_processing'] is expected
    records.data[PATH]['kling_audio_processing']=not enabled
    assert client.post(f'/api/uploads/{jobid}/complete',headers=headers).status_code==202
    job=records.data[f'jobs/{jobid}.json']
    assert job['audio_processing'] is expected and job['restore_original_audio'] is True
    assert job['face_mesh'] is False and job['depth_guidance'] is False
    assert not provider.submissions


def test_legacy_accepted_kling_ticket_keeps_saved_audio_off(api):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    records.data[PATH]={'kling4_enabled':True,'kling_audio_processing':False}
    response=client.post('/api/uploads',headers=headers,json=options())
    assert response.status_code==201,response.text
    jobid=response.json()['id']
    records.data[PATH]['kling_audio_processing']=True
    assert client.post(f'/api/uploads/{jobid}/complete',headers=headers).status_code==202
    assert records.data[f'jobs/{jobid}.json']['audio_processing'] is False
    assert not provider.submissions


def test_multipart_kling_keeps_audio_processing_with_original_audio_restore(api):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    records.data[PATH]={'kling4_enabled':True,'audio_processing':False,'kling_audio_processing':True}
    image=BytesIO();Image.new('RGB',(256,256),'blue').save(image,format='PNG')
    response=client.post('/api/jobs',headers=headers,data={'setup':json.dumps(options()['setup'])},
        files={'clip':('video.mp4',b'video'),'reference':('photo.png',image.getvalue(),'image/png')})
    assert response.status_code==202,response.text
    job=records.data[f"jobs/{response.json()['id']}.json"]
    assert job['audio_processing'] is True and job['restore_original_audio'] is True
    assert not provider.submissions


@pytest.mark.parametrize('model',[KLING,MINIMAX])
@pytest.mark.parametrize('minimax_audio,kling_audio',[(False,False),(True,False),(False,True),(True,True)])
def test_audio_defaults_are_independent_between_models(api,model,minimax_audio,kling_audio):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    records.data[PATH]={'kling4_enabled':True,'audio_processing':minimax_audio,
                        'kling_audio_processing':kling_audio}
    body=options();body['setup']['generation_model']=model
    response=client.post('/api/uploads',headers=headers,json=body)
    assert response.status_code==201,response.text
    jobid=response.json()['id']
    assert client.post(f'/api/uploads/{jobid}/complete',headers=headers).status_code==202
    assert records.data[f'jobs/{jobid}.json']['audio_processing'] is (kling_audio if model==KLING else minimax_audio)
    assert not provider.submissions


def test_admin_can_save_kling_audio_without_changing_minimax_and_checks_access(api,monkeypatch):
    client,records,cloud,provider=api
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS','owner@example.com')
    monkeypatch.setattr(gateway,'verify_account',lambda token,_:{'email':
        'owner@example.com' if token=='owner' else 'user@example.com'})
    url='/api/commerce/admin/pipeline';headers={'X-Firebase-Token':'owner'}
    response=client.post(url,headers=headers,json={'revision':0,'kling_audio_processing':True})
    assert response.status_code==200,response.text
    assert response.json()['kling_audio_processing'] is True and response.json()['audio_processing'] is False
    response=client.post(url,headers=headers,json={'revision':1,'audio_processing':True})
    assert response.json()['audio_processing'] is True and response.json()['kling_audio_processing'] is True
    response=client.post(url,headers=headers,json={'revision':2,'kling_audio_processing':False})
    assert response.json()['audio_processing'] is True and response.json()['kling_audio_processing'] is False
    assert client.post(url,headers=headers,json={'revision':3,'kling_audio_processing':1}).status_code==422
    assert client.post(url,headers={'X-Firebase-Token':'user'},json={'revision':3,'kling_audio_processing':True}).status_code==403
    assert client.post(url,headers=headers,json={'revision':0,'kling_audio_processing':True}).status_code==409


def test_quote_is_model_specific_and_pinned_after_catalog_change(api, monkeypatch):
    _, records, _, _ = api
    enable_credits(records,monkeypatch,1000)
    records.data['commerce/catalog.json'] = {**default_catalog(),'credits_per_generation':150,
                                            'kling_credits_per_generation':450,'revision':3}
    setup = Setup.model_validate({**options(30)['setup'], 'generation_credit_quote':900})
    quote = generation_quote(records,setup)
    assert quote == {'model':KLING,'credits':900,'credits_for_15_seconds':450,'catalog_revision':3}
    records.data['commerce/catalog.json']['kling_credits_per_generation'] = 600
    session = {'access_mode':'credits','account_id':ACCOUNT_ID}
    assert gateway.check_generation_funding(records,setup,session,pinned_quote=quote) == quote
    setup = Setup.model_validate({**options(30)['setup'], 'generation_credit_quote':1200})
    with pytest.raises(PublicError) as error:
        gateway.check_generation_funding(records,setup,session)
    assert error.value.status_code == 402
    stale = Setup.model_validate({**options()['setup'],'generation_credit_quote':450})
    with pytest.raises(PublicError) as error:
        generation_quote(records,stale)
    assert error.value.code == 'generation_price_changed'


def test_legacy_markup_migration_and_admin_preservation(api, monkeypatch):
    _, records, *_ = api
    assert kling_credit_rate({'credits_per_generation':150}) == 362
    assert generation_credit_cost(30,362,generation_model=KLING) == 724
    with pytest.raises(PublicError):
        generation_credit_cost(30,150)
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS','owner@example.com')
    actor={'email':'owner@example.com'}
    initial=default_catalog()
    records.data['commerce/catalog.json']={**initial,'kling_credits_per_generation':450}
    saved=save_catalog(records,actor,0,initial['packs'],180)
    assert saved['kling_credits_per_generation'] == 450
    assert public_catalog(records)['kling_credits_per_second'] == 30
    assert not pipeline_settings(records)['kling4_enabled']
    save_pipeline_settings(records,actor,0,kling4_enabled=True)
    save_pipeline_settings(records,actor,1,hide_audio_toggle=True)
    assert pipeline_settings(records)['kling4_enabled'] is True


def test_kling_model_and_mesh_policy_survive_library_summary():
    setup=Setup.model_validate(options()['setup'])
    job={'setup':setup.model_dump(),**generation_snapshot(setup),
         'generation_settings':{'model':KLING,'resolution':'720p'}}
    summary=settings_used(job)
    assert summary['model']=='Kling 4.0' and summary['requested_resolution']=='720p'
    assert summary['face_mesh'] is False and summary['depth_guidance'] is False


def test_credit_kling_cannot_omit_the_displayed_quote(api, monkeypatch):
    _, records, cloud, provider = api
    enable_credits(records,monkeypatch,2000)
    setup=Setup.model_validate(options()['setup'])
    with pytest.raises(PublicError) as error:
        gateway.check_generation_funding(records,setup,{'access_mode':'credits','account_id':ACCOUNT_ID})
    assert error.value.code == 'generation_quote_required'
    assert not cloud.enqueued and not provider.submissions


@pytest.mark.parametrize('failed',[False,True])
def test_thirty_second_kling_uses_pinned_debit_once_and_refunds_known_failure(api, monkeypatch, failed):
    client, records, cloud, provider = api
    enable_credits(records,monkeypatch,2000)
    jobid='e'*32
    records.data[f'jobs/{jobid}.json']={
        'id':jobid,'status':'ready','access_mode':'credits','account_id':ACCOUNT_ID,
        'owner':'owner','duration':30,'generation_model':KLING,'generation_credits':600,
        'guide_uri':'gs://private-app/body-guide.mp4','reference_uri':'gs://private-app/ref.jpg',
        'face_mesh':False,'audio_processing':False,'mask_style':'identity_overlay_20',
        'setup':{**options(30)['setup'],'face_mesh':False,'enhanced_character_masking':True,
                 'auto_generate':True,'easy_mode':True},
    }
    monkeypatch.setattr(gateway,'internal_pipeline',lambda *_:None)
    if failed:
        def reject(*_args,**_kwargs):
            raise PublicError('Provider rejected request.',400)
        monkeypatch.setattr(provider,'submit',reject)
    response=client.post('/internal/generate',json={'job_id':jobid})
    assert records.data[f'accounts/{ACCOUNT_ID}/wallet.json']['balance']==(2000 if failed else 1400)
    if failed:
        assert records.data[f'jobs/{jobid}.json']['status']=='failed'
        return
    assert response.status_code==200
    assert provider.submissions[0]['model']==KLING
    assert 'duration' not in provider.submissions[0]
    assert client.post('/internal/generate',json={'job_id':jobid}).status_code==200
    assert len(provider.submissions)==1
    polled=[]
    def status(_key,task,**kwargs):
        polled.append(kwargs)
        return {'id':task,'model':KLING,'status':'completed',
                'usage':{'credits':5352},'output':{'video_urls':['https://cdn.reapi.ai/result.mp4']}}
    monkeypatch.setattr(provider,'status',status)
    token=records.data[f'jobs/{jobid}.json']['provider_poll_token']
    assert client.post('/internal/poll-provider',json={'job_id':jobid,'task_token':token}).status_code==200
    assert polled==[{'generation_model':KLING}]
    assert records.data[f'jobs/{jobid}.json']['status']=='finalizing'
    assert ('finish',jobid) in cloud.enqueued
