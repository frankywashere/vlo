from dataclasses import replace
from io import BytesIO
import importlib.util
from pathlib import Path
from PIL import Image
import pytest
import swapmein.app as gateway
from test_api import api

def photo():
    out=BytesIO();Image.new('RGB',(256,256),'red').save(out,'PNG');return out.getvalue()

def test_authenticated_cleanup_validates_before_computing_and_bounds_daily_usage(api,monkeypatch):
    client,records,cloud,_=api
    monkeypatch.setattr(gateway,'settings',replace(gateway.settings,reference_cleanup_url='https://cleanup.run.app'))
    monkeypatch.setattr(gateway,'verify_account',lambda token,project:{'account_id':'a'*64,'email':'user@example.com'})
    calls=[]
    monkeypatch.setattr(cloud,'clean_reference',lambda raw:calls.append(raw)or b'cleaned-jpeg',raising=False)
    response=client.post('/api/references/clean',files={'reference':('image.png',photo(),'image/png')})
    assert response.status_code==200 and response.headers['cache-control']=='no-store'
    assert Image.open(BytesIO(calls[0])).format=='JPEG'
    assert records.data['control/reference-cleanup.json']['count']==1
    result=client.post('/api/references/clean',files={'reference':('bad.png',b'not-image','image/png')})
    assert result.status_code==400 and len(calls)==1
    records.data['control/reference-cleanup.json']['accounts']['a'*64]=100
    result=client.post('/api/references/clean',files={'reference':('image.png',photo(),'image/png')})
    assert result.status_code==429 and len(calls)==1

def test_cleanup_requires_account_before_compute(api,monkeypatch):
    client,_,_,_=api
    def reject(*_):raise gateway.PublicError('Sign in',401)
    monkeypatch.setattr(gateway,'verify_account',reject)
    response=client.post('/api/references/clean',files={'reference':('image.png',photo(),'image/png')})
    assert response.status_code==401

def worker_module():
    path=Path(__file__).parents[2]/'reference_cleanup'/'app.py'
    spec=importlib.util.spec_from_file_location('reference_cleanup_worker',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def test_worker_preserves_subject_rgb_and_geometry_with_gray_outside_mask():
    worker=worker_module()
    class MaskSession:
        def predict(self,image):
            mask=Image.new('L',image.size,0);mask.paste(255,(64,64,192,192));return [mask]
    worker._session=MaskSession()
    image=Image.open(BytesIO(worker.clean_reference(photo())))
    assert image.size==(256,256)
    assert all(abs(channel-128)<=2 for channel in image.getpixel((10,10)))
    center=image.getpixel((128,128));assert center[0]>250 and center[1]<4 and center[2]<4

def test_worker_rejects_no_foreground_and_oversized_payload():
    worker=worker_module()
    class EmptySession:
        def predict(self,image):return [Image.new('L',image.size,0)]
    worker._session=EmptySession()
    with pytest.raises(worker.HTTPException)as error:worker.clean_reference(photo())
    assert error.value.status_code==422
    with pytest.raises(worker.HTTPException)as error:worker.clean_reference(b'x'*(worker.MAX_BYTES+1))
    assert error.value.status_code==413
