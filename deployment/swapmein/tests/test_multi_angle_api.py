"""Six-photo grouping, resolution quotes, and immutable hosted routing."""
import copy
import pytest
from swapmein.commerce import default_catalog, generation_quote
from swapmein.media import Setup
from swapmein.pipeline_settings import PATH, pipeline_settings, save_pipeline_settings
from swapmein.self_hosted_route import generation_route_snapshot
from test_api import api, authenticated
from test_creation_credit_gate import enable_credits, ACCOUNT_ID


def request():
    return {'source_bytes':2048,'reference_sizes':[1024]*6,
            'setup':{'trim':[0,15],'generation_resolution':'2K','anchor':{'x':.4,'y':.5,'time':1},
                     'reference_indexes':[0,1,2,3,4,5]}}


def test_single_character_accepts_six_angles_when_multicharacters_disabled(api):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    records.data[PATH]={'multi_characters':False}
    r=client.post('/api/uploads',headers=headers,json=request());assert r.status_code==201,r.text
    ticket=r.json();assert len(ticket['references'])==6
    r=client.post(f"/api/uploads/{ticket['id']}/complete",headers=headers)
    assert r.status_code==202,r.text
    job=records.data[f"jobs/{ticket['id']}.json"]
    assert job['generation_resolution']=='2K' and job['setup']['reference_indexes']==list(range(6))
    assert len(job['reference_uris'])==6 and not provider.submissions


@pytest.mark.parametrize('bad',[
    {'reference_sizes':[1024]*7},
    {'setup':{'reference_indexes':[0,1,2,3,4,5,6]}},
    {'setup':{'reference_indexes':[0,0]}},
    {'setup':{'reference_indexes':[0,True]}},
    {'setup':{'reference_indexes':[0,5]},'reference_sizes':[1024]*2},
])
def test_invalid_reference_groups_fail_before_any_storage_or_paid_request(api,bad):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    body=request()
    for key,value in bad.items():
        if key=='setup':body['setup'].update(value)
        else:body[key]=value
    r=client.post('/api/uploads',headers=headers,json=body)
    assert r.status_code==422,r.text
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions


def test_2k_six_photo_quote_is_pinned_through_upload_complete_and_debit(api,monkeypatch):
    client,records,cloud,provider=api;headers,owner=authenticated(api)
    enable_credits(records,monkeypatch,1000)
    records.data['sessions/'+owner+'.json'].update(access_mode='credits',account_id=ACCOUNT_ID)
    records.data['commerce/catalog.json']={**default_catalog(), 'minimax_2k_credits_per_generation':300,
        'minimax_extra_reference_credits':7}
    body=request();body['setup']['generation_credit_quote']=307
    r=client.post('/api/uploads',headers=headers,json=body);assert r.status_code==201,r.text
    jobid=r.json()['id'];manifest=records.data[f'uploads/{jobid}/manifest.json']
    assert manifest['generation_quote']['reference_count']==6
    assert manifest['generation_quote']['resolution']=='2K'
    assert manifest['generation_quote']['extra_reference_credits']==7
    records.data['commerce/catalog.json']['minimax_2k_credits_per_generation']=900
    r=client.post(f'/api/uploads/{jobid}/complete',headers=headers);assert r.status_code==202,r.text
    job=records.data[f'jobs/{jobid}.json'];assert job['generation_credits']==307
    job.update(status='ready',duration=15,guide_uri='gs://private-app/hd-guide.mp4',
        tracking_reviewed=True,mask_style='identity_overlay_20',normalized_anchor={'x':.4,'y':.5,'time':1})
    for _ in range(2):
        r=client.post(f'/api/jobs/{jobid}/generate',headers=headers,json={'approved':True});assert r.status_code==200,r.text
    assert len(provider.submissions)==1
    payload=provider.submissions[0]
    assert payload['resolution']=='2K' and len(payload['reference_image_urls'])==6
    assert 'same replacement character' in payload['prompt']
    assert records.data[f'accounts/{ACCOUNT_ID}/wallet.json']['balance']==693


def test_visible_quotes_include_resolution_and_sixth_photo_cost(api):
    _,records,*_=api
    records.data['commerce/catalog.json']={**default_catalog(),'minimax_2k_credits_per_generation':300,
        'minimax_extra_reference_credits':7}
    data=request()['setup'];data['generation_credit_quote']=307
    quote=generation_quote(records,Setup.model_validate(data))
    assert quote['credits']==307 and quote['base_credits']==300
    # The same six photos on Kling add no MiniMax-only image charge.
    data.update(generation_model='kling-4-0',generation_resolution='720p',generation_credit_quote=362)
    assert generation_quote(records,Setup.model_validate(data))['credits']==362


def test_2k_switch_blocks_new_user_uploads_but_preserves_accepted_jobs(api,monkeypatch):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    r=client.post('/api/uploads',headers=headers,json=request());assert r.status_code==201,r.text
    accepted=r.json()['id']
    records.data[PATH]={'minimax_2k_enabled':False}
    assert client.get('/api/config').json()['minimax_2k_enabled'] is False
    sizes=len(cloud.upload_sizes)
    r=client.post('/api/uploads',headers=headers,json=request())
    assert r.status_code==409 and r.json()['code']=='minimax_2k_disabled'
    assert len(cloud.upload_sizes)==sizes and not provider.submissions
    r=client.post(f'/api/uploads/{accepted}/complete',headers=headers)
    assert r.status_code==202,r.text
    assert records.data[f'jobs/{accepted}.json']['generation_resolution']=='2K'


def test_disabled_2k_keeps_standard_resolution_and_staff_tests_available(api,monkeypatch):
    client,records,*_=api;headers,_=authenticated(api)
    records.data[PATH]={'minimax_2k_enabled':False}
    standard=request();standard['setup']['generation_resolution']='768P'
    assert client.post('/api/uploads',headers=headers,json=standard).status_code==201
    monkeypatch.setattr('swapmein.app.staff_role',lambda *_:'owner')
    assert client.post('/api/uploads',headers=headers,json=request()).status_code==201


def test_2k_switch_persists_across_unrelated_admin_edits(api,monkeypatch):
    client,records,*_=api
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS','owner@example.com')
    actor={'email':'owner@example.com'}
    assert pipeline_settings(records)['minimax_2k_enabled'] is True
    save_pipeline_settings(records,actor,0,minimax_2k_enabled=False)
    save_pipeline_settings(records,actor,1,hide_audio_toggle=True)
    assert pipeline_settings(records)['minimax_2k_enabled'] is False


@pytest.mark.parametrize('route',['google_h3','paperspace_h3'])
def test_options_unsupported_by_private_gpu_use_the_hosted_route_explicitly(route):
    targets=[{'reference_index':0},{'reference_index':1}]
    assert generation_route_snapshot({'targets':targets},route)['multi_person_generation_route']==route
    assert generation_route_snapshot({'targets':targets,'generation_resolution':'2K'},route)['multi_person_generation_route']=='current'
    targets[0]['reference_indexes']=[0,2]
    assert generation_route_snapshot({'targets':targets},route)['multi_person_generation_route']=='current'
