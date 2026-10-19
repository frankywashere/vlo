"""Cutout alpha survives both upload routes and reaches the provider as PNG."""
from io import BytesIO
import json

from PIL import Image
import pytest
from test_api import api, authenticated
from test_multi_person_api import upload_options


def cutout():
    image = Image.new("RGBA", (256,256), (0,0,0,0))
    image.paste((220,80,40,255),(64,64,192,192))
    image.putpixel((63,64),(220,80,40,128))
    stream=BytesIO();image.save(stream,"PNG")
    return stream.getvalue()


@pytest.mark.parametrize("route", ["multipart", "direct"])
def test_upload_preserves_png_alpha_and_content_type(api, monkeypatch, route):
    client, records, cloud, provider = api
    headers, _ = authenticated(api)
    uploads=[]
    def upload(bucket,key,path,mime):
        if "reference" in key:
            uploads.append((key,mime,path.read_bytes()))
        return f"gs://{bucket}/{key}"
    monkeypatch.setattr(cloud,"upload",upload)
    if route=="multipart":
        response=client.post("/api/jobs",headers=headers,
            files={"clip":("clip.mp4",b"source","video/mp4"),"reference":("cutout.png",cutout(),"image/png")},
            data={"setup":json.dumps({"trim":[0,5],"anchor":{"x":.5,"y":.5,"time":1}})})
    else:
        monkeypatch.setattr(cloud,"download",lambda _uri,path,_maximum:path.write_bytes(cutout()))
        ticket=client.post("/api/uploads",headers=headers,json=upload_options()).json()
        response=client.post(f"/api/uploads/{ticket['id']}/complete",headers=headers)
    assert response.status_code==202,response.text
    assert len(uploads)==(1 if route=="multipart" else 2)
    for key,mime,data in uploads:
        assert key.endswith(".png") and mime=="image/png"
        with Image.open(BytesIO(data)) as image:
            assert image.format=="PNG" and image.mode=="RGBA"
            assert image.getpixel((0,0))[3]==0
            assert image.getpixel((63,64))==(220,80,40,128)
            assert image.getpixel((128,128))==(220,80,40,255)
    job_id=response.json()["id"]
    job=records.data[f"jobs/{job_id}.json"]
    assert all(uri.endswith(".png") for uri in job["reference_uris"])
    job.update(status="ready",guide_uri="gs://private-app/guide.mp4",duration=5,tracking_reviewed=True)
    if route=="direct":
        job['normalized_targets']=[{**target,"anchor":{**target['anchor'],"time":target['anchor']['time']-1}}
            for target in job['setup']['targets']]
    result=client.post(f"/api/jobs/{job_id}/generate",headers=headers,json={"approved":True})
    assert result.status_code==200,result.text
    assert len(provider.submissions)==1
    assert all(".png?" in url for url in provider.submissions[0]['reference_image_urls'])
