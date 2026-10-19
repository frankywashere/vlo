"""Initial Easy Mode misses obey the same bounded policy as reviewed misses."""
import copy
from contextlib import contextmanager
from io import BytesIO
import json
import zipfile
import numpy as np
from PIL import Image
import pytest
from swapmein.initial_mask_recovery import recover_initial_selection
from swapmein.multi_tracking import style_targets
from swapmein.provider import generation_payload
from swapmein.errors import PublicError


def archive(labels):
    out=BytesIO()
    with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
        for frame in range(96):
            image=BytesIO();Image.fromarray(labels).save(image,'PNG')
            z.writestr(f'labels/labels_{frame:05d}.png',image.getvalue())
    return out.getvalue()


@pytest.mark.parametrize('retry,outcome',[(False,'coordinate_fallback'),(True,'recovered'),(True,'coordinate_fallback'),(True,'conflict'),(True,'invalid')])
def test_initial_miss_repairs_only_selected_shot_or_preserves_coordinate_fallback(tmp_path,monkeypatch,retry,outcome):
    metadata={'width':8,'height':8,'frames':96,'duration':4,'anchor':{'frame':0},'scene_cuts':[2]}
    labels=np.zeros((8,8),np.uint16);labels[3:7,1:3]=7
    masks=tmp_path/'raw.zip';masks.write_bytes(archive(labels))
    targets=style_targets([{'id':'left','reference_index':0,'anchor':{'x':.2,'y':.6,'time':1}},
                          {'id':'right','reference_index':1,'anchor':{'x':.8,'y':.2,'time':95/24}}])
    job={'id':'a'*32,'retry_missing_masks_with_sam':retry,'normalized_uri':'gs://original-rgb'}
    calls=[]; objects={job['normalized_uri']:b'original-rgb'}
    class Records:
        def read(self,_):return copy.deepcopy(job),1
    class Cloud:
        def upload(self,bucket,key,path,mime):
            uri='gs://'+bucket+'/'+key;objects[uri]=path.read_bytes();return uri
        def download(self,uri,path,maximum):path.write_bytes(objects[uri])
        def copy(self,uri,bucket,key):
            result='gs://'+bucket+'/'+key;objects[result]=objects[uri];return result
        def worker_output(self,result,run,name):return result[name]
        @contextmanager
        def worker(self,kind):
            assert kind=='gpu'
            def invoke(endpoint,payload,timeout):
                calls.append((endpoint,payload))
                assert endpoint=='/recover-masks' and objects[payload['input_uri']]==b'bounded-rgb'
                assert payload['selections']==[{'segment_id':'initial-right','start_frame':48,'end_frame':96,'frame':95,'x':.8,'y':.2,'label':2}]
                out=labels.copy();out[out==7]=1
                if outcome=='recovered':out[1:3,5:7]=2
                objects['gs://recovered.zip']=archive(out)
                objects['gs://report.json']=json.dumps({'selections':[{'segment_id':'initial-right','outcome':outcome}]}).encode()
                return {'masks.zip':'gs://recovered.zip','recovery.json':'gs://report.json'}
            yield invoke
    class Settings:worker_bucket='worker'
    class Pipeline:
        settings=Settings();cloud=Cloud();records=Records()
        def update_job(self,_,**fields):job.update(fields);return copy.deepcopy(job)
    def sam_input(source,destination):
        assert source.read_bytes()==b'original-rgb';destination.write_bytes(b'bounded-rgb')
    monkeypatch.setattr('swapmein.mask_recovery.sam_input_video',sam_input)
    if outcome in {'conflict','invalid'}:
        with pytest.raises(PublicError):recover_initial_selection(Pipeline(),job,masks,metadata,targets,tmp_path,lambda:60)
        return
    uri,coverage,updated=recover_initial_selection(Pipeline(),job,masks,metadata,targets,tmp_path,lambda:60)
    assert len(calls)==int(retry)
    assert len(coverage)==96
    assert updated[0]==targets[0]  # Do not change correctly matched references/anchors.
    assert updated[1].get('coordinate_fallback',False)==(outcome!='recovered')
    assert job['coordinate_fallback_sections']==(outcome!='recovered')
    with zipfile.ZipFile(BytesIO(objects[uri])) as z:
        a=np.array(Image.open(BytesIO(z.read('labels/labels_00095.png'))))
    assert np.all(a[3:7,1:3]==1)
    assert bool(np.any(a==2))==(outcome=='recovered')
    prompt=generation_payload(4,['https://left','https://right'],'https://video',True,
        targets=updated,mask_style='cyan_overlay_multi_20')['prompt']
    assert ('Person 2 has no detected selection mask' in prompt)==(outcome!='recovered')


@pytest.mark.parametrize("missing_index", [0, 1])
@pytest.mark.parametrize("style", ["depth_multi", "cyan_overlay_multi_20"])
def test_coordinate_prompt_keeps_guidance_for_other_masked_targets(missing_index, style):
    targets=[{"id":str(i),"reference_index":i,"anchor":{"x":.2+i*.5,"y":.5,"time":1},
              **({"coordinate_fallback":True} if i==missing_index else {})} for i in range(2)]
    prompt=generation_payload(4,["https://one","https://two"],"https://video",True,targets=targets,mask_style=style)["prompt"]
    assert f"Person {missing_index+1} has no detected selection mask" in prompt
    assert ("Colored depth imagery marks only selections with detected masks" in prompt)==(style=="depth_multi")
    assert ("Remove every depth-map color" in prompt)==(style=="depth_multi")
