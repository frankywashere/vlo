import json
from io import BytesIO
from PIL import Image
import pytest
import swapmein.app as gateway
from swapmein.media import Setup
from swapmein.pipeline_settings import PATH, face_mesh_snapshot
from test_api import api, authenticated, upload_options


@pytest.mark.parametrize('masked',[False,True])
@pytest.mark.parametrize('admin',[False,True])
@pytest.mark.parametrize('visible',[False,True])
@pytest.mark.parametrize('override',[None,False,True])
def test_master_off_blocks_every_override_and_preserves_mode_defaults(masked,admin,visible,override):
    defaults={'face_mesh_enabled':False,'face_mesh_masked_default':True,
              'face_mesh_unmasked_default':True,'face_mesh_user_control':visible}
    setup=Setup(trim=[0,4],anchor={'x':.5,'y':.5,'time':0},enhanced_character_masking=masked,face_mesh=override)
    assert face_mesh_snapshot(defaults,setup,is_admin=admin)=={'face_mesh':False}
    defaults['face_mesh_enabled']=True
    expected=override if (admin or visible) and override is not None else True
    assert face_mesh_snapshot(defaults,setup,is_admin=admin)=={'face_mesh':expected}


def test_master_and_public_visibility_are_independent_permissioned_controls(api,monkeypatch):
    from test_staff_permissions import setup_staff
    client,records=setup_staff(api,monkeypatch,[])
    url='/api/commerce/admin/pipeline'
    assert client.get('/api/config').json()['face_mesh_options']['face_mesh_enabled'] is True
    body={'revision':0,'face_mesh_enabled':False}
    assert client.post(url,json=body).status_code==403
    headers={'X-Firebase-Token':'owner'}
    result=client.post(url,headers=headers,json=body)
    assert result.status_code==200
    options=client.get('/api/config').json()['face_mesh_options']
    assert options['face_mesh_enabled'] is False and options['face_mesh_user_control'] is False
    result=client.post(url,headers=headers,json={'revision':1,'face_mesh_user_control':True})
    assert result.json()['face_mesh_enabled'] is False
    assert result.json()['face_mesh_masked_default'] is True
    assert result.json()['face_mesh_unmasked_default'] is False
    assert client.post(url,headers=headers,json={'revision':1,'face_mesh_enabled':True}).status_code==409
    assert client.post(url,headers=headers,json={'revision':2,'face_mesh_enabled':1}).status_code==422


@pytest.mark.parametrize('direct',[False,True])
@pytest.mark.parametrize('masked',[False,True])
@pytest.mark.parametrize('admin',[False,True])
@pytest.mark.parametrize('visible',[False,True])
def test_new_jobs_snapshot_master_off_in_both_upload_routes(api,monkeypatch,direct,masked,admin,visible):
    client,records,_,_=api;headers,owner=authenticated(api)
    monkeypatch.setenv('COMMERCE_OWNER_EMAILS','owner@example.com')
    records.data[f'sessions/{owner}.json']['email']='owner@example.com' if admin else 'user@example.com'
    records.data[PATH]={'revision':1,'face_mesh_enabled':False,'face_mesh_masked_default':True,
                        'face_mesh_unmasked_default':True,'face_mesh_user_control':visible}
    body=upload_options();body['setup'].update(enhanced_character_masking=masked,face_mesh=True)
    if direct:
        ticket=client.post('/api/uploads',headers=headers,json=body).json()
        response=client.post(f"/api/uploads/{ticket['id']}/complete",headers=headers)
    else:
        image=BytesIO();Image.new('RGB',(256,256),'blue').save(image,'PNG')
        response=client.post('/api/jobs',headers=headers,files={'clip':('clip.mp4',b'video','video/mp4'),
            'reference':('photo.png',image.getvalue(),'image/png')},data={'setup':json.dumps(body['setup'])})
    assert response.status_code==202,response.text
    job=records.data[f"jobs/{response.json()['id']}.json"]
    assert job['face_mesh'] is False
    records.data[PATH]['face_mesh_enabled']=True
    assert job['face_mesh'] is False
