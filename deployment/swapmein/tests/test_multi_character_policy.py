import copy
import json
from io import BytesIO
from PIL import Image
import pytest
import swapmein.app as gateway
from swapmein.pipeline_settings import PATH
from test_api import api, authenticated, ready_job, upload_options as single_upload
from test_multi_person_api import upload_options as multi_upload


def test_multi_characters_defaults_on_and_requires_permission_and_current_revision(api,monkeypatch):
    from test_staff_permissions import setup_staff
    client,records=setup_staff(api,monkeypatch,[])
    url='/api/commerce/admin/pipeline'
    assert client.get('/api/config').json()['multi_characters'] is True
    assert client.post(url,json={'revision':0,'multi_characters':False}).status_code==403
    result=client.post(url,headers={'X-Firebase-Token':'owner'},json={'revision':0,'multi_characters':False})
    assert result.status_code==200
    assert client.get('/api/config').json()['multi_characters'] is False
    assert client.post(url,headers={'X-Firebase-Token':'owner'},json={'revision':0,'multi_characters':True}).status_code==409
    assert client.post(url,headers={'X-Firebase-Token':'owner'},json={'revision':1,'multi_characters':1}).status_code==422
    result=client.post(url,headers={'X-Firebase-Token':'owner'},json={'revision':1,'hide_audio_toggle':True})
    assert result.json()['multi_characters'] is False


@pytest.mark.parametrize('easy',[False,True])
@pytest.mark.parametrize('enhanced',[False,True])
@pytest.mark.parametrize('mesh',[False,True])
@pytest.mark.parametrize('kind',['multiple','shared_reference','all_people','extra_reference'])
def test_disabled_policy_blocks_all_new_direct_upload_modes_before_storage_or_workers(api,easy,enhanced,mesh,kind):
    client,records,cloud,provider=api;headers,_=authenticated(api)
    records.data[PATH]={'revision':1,'multi_characters':False}
    body=multi_upload()
    body['setup'].update(easy_mode=easy,enhanced_character_masking=enhanced,face_mesh=mesh)
    if kind=='shared_reference':
        body['reference_sizes']=[1024]
        for target in body['setup']['targets']:target['reference_index']=0
    if kind=='all_people':
        body['reference_sizes']=[1024];body['setup'].pop('targets');body['setup']['replace_all_people']=True
    if kind=='extra_reference':body['setup']['targets']=body['setup']['targets'][1:]
    response=client.post('/api/uploads',headers=headers,json=body)
    assert response.status_code==409,response.text
    assert response.json()['code']=='single_character_only'
    assert not cloud.upload_sizes and not cloud.enqueued and not provider.submissions


def test_stale_upload_cannot_complete_after_multi_is_disabled(api):
    client,records,cloud,_=api;headers,_=authenticated(api)
    ticket=client.post('/api/uploads',headers=headers,json=multi_upload()).json()
    records.data[PATH]={'revision':1,'multi_characters':False}
    response=client.post(f"/api/uploads/{ticket['id']}/complete",headers=headers)
    assert response.status_code==409,response.text
    assert f"jobs/{ticket['id']}.json" not in records.data
    assert not cloud.enqueued


def test_multipart_cannot_bypass_single_character_mode_with_shared_reference(api):
    client,records,cloud,_=api;headers,_=authenticated(api)
    records.data[PATH]={'multi_characters':False}
    setup=multi_upload()['setup']
    for target in setup['targets']:target['reference_index']=0
    image=BytesIO();Image.new('RGB',(256,256),'blue').save(image,'PNG')
    response=client.post('/api/jobs',headers=headers,files={'clip':('clip.mp4',b'video','video/mp4'),
        'reference':('ref.png',image.getvalue(),'image/png')},data={'setup':json.dumps(setup)})
    assert response.status_code==409,response.text
    assert not cloud.enqueued


@pytest.mark.parametrize('direct',[False,True])
def test_single_person_accepted_and_policy_snapshotted_off(api,direct):
    client,records,_,_=api;headers,_=authenticated(api)
    records.data[PATH]={'multi_characters':False}
    if direct:
        ticket=client.post('/api/uploads',headers=headers,json=single_upload()).json()
        response=client.post(f"/api/uploads/{ticket['id']}/complete",headers=headers)
    else:
        image=BytesIO();Image.new('RGB',(256,256),'blue').save(image,'PNG')
        response=client.post('/api/jobs',headers=headers,files={'clip':('clip.mp4',b'video','video/mp4'),
            'reference':('ref.png',image.getvalue(),'image/png')},data={'setup':json.dumps(single_upload()['setup'])})
    assert response.status_code==202,response.text
    assert records.data[f"jobs/{response.json()['id']}.json"]['multi_characters'] is False


@pytest.mark.parametrize('snapshot',[None,True,False])
def test_existing_generation_uses_its_saved_policy_not_current_global_switch(api,snapshot):
    client,records,_,provider=api;headers,owner=authenticated(api)
    job_id=ready_job(records,owner);job=records.data[f'jobs/{job_id}.json']
    job['setup']=multi_upload()['setup'];job['normalized_targets']=copy.deepcopy(job['setup']['targets'])
    for target in job['normalized_targets']:target['anchor']['time']-=1
    job['reference_uris']=['gs://private-app/one.jpg','gs://private-app/two.jpg']
    if snapshot is not None:job['multi_characters']=snapshot
    records.data[PATH]={'multi_characters':False}
    response=client.post(f'/api/jobs/{job_id}/generate',headers=headers,json={'approved':True})
    assert response.status_code==(409 if snapshot is False else 200),response.text
    assert bool(provider.submissions)==(snapshot is not False)


@pytest.mark.parametrize('route',['/api/uploads','/api/jobs'])
@pytest.mark.parametrize('access',['credits','api-key'])
def test_policy_applies_to_both_funding_routes(api,route,access):
    client,records,cloud,_=api;headers,owner=authenticated(api)
    records.data[f'sessions/{owner}.json']['access_mode']=access
    records.data[PATH]={'multi_characters':False}
    body=multi_upload()
    if route=='/api/uploads':response=client.post(route,headers=headers,json=body)
    else:
        for target in body['setup']['targets']:target['reference_index']=0
        response=client.post(route,headers=headers,files={'clip':('video.mp4',b'video','video/mp4'),'reference':('image.jpg',b'photo','image/jpeg')},data={'setup':json.dumps(body['setup'])})
    assert response.status_code==409,response.text
    assert response.json()['code']=='single_character_only'
    assert not cloud.enqueued
