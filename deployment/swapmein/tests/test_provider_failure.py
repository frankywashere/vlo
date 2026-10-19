import json
import pytest
import swapmein.app as gateway
from swapmein.provider_failure import provider_failure
from test_api import api


@pytest.mark.parametrize('error,category,code', [
    ({'code':80006,'message':'Content policy violation'}, 'content_rejected',80006),
    ({'code':'80006','message':'FaceDetectionSuspect'}, 'face_rejected',80006),
    ({'code':80003,'message':'Faces were rejected'}, 'face_rejected',80003),
    ({'code':80003,'message':'Face mesh processing failed'}, 'generation_failed',80003),
    ({'code':True,'message':'error'}, 'generation_failed',None),
    (None,'generation_failed',None),
    ('NSFW','generation_failed',None),
])
def test_failure_requires_explicit_reason(error,category,code):
    assert provider_failure({'error':error}) == {'code':code,'category':category}


@pytest.mark.parametrize('code,message,expected', [(80006,'flagged','Content rejected'), (80003,'FaceDetectionSuspect','Face rejected'), (80003,'network failed','generation failed')])
def test_poll_preserves_public_reason_for_library_without_leaking_provider_text(api,monkeypatch,capsys,code,message,expected):
    _,records,_,provider=api
    job_id='c'*32
    job={'id':job_id,'status':'generating','provider':'reAPI','provider_task':'task_example','access_mode':'api-key'}
    records.data[f'jobs/{job_id}.json']=job
    private='private-url-secret-key'
    monkeypatch.setattr(provider,'status',lambda *_:{'status':'failed','error':{'code':code,'message':message+' '+private}})
    monkeypatch.setattr(gateway,'selected_provider',lambda *_:provider)
    result=gateway.provider_status(job_id,job,'private-api-key')
    saved=records.data[f'jobs/{job_id}.json']
    assert expected in result['message']
    assert saved['provider_state']=='failed'
    # Credit-funded views must preserve only the same safe explanation.
    credit_view=gateway.view({**saved,'access_mode':'credits'})
    assert expected in credit_view['message']
    assert 'credits were returned' in credit_view['message']
    logs=capsys.readouterr().out
    assert private not in json.dumps(saved)+json.dumps(result)+json.dumps(credit_view)+logs
    assert 'provider_error_code' in logs
