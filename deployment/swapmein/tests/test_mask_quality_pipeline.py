"""Reviewed mask checks preserve approvals only for the checked guide version."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.config import Settings
from swapmein.pipeline import Pipeline


def fixture_job(tmp_path):
    frames = 3
    maps = np.zeros((64,64),np.uint16)
    maps[4:24,0:24]=1; maps[34:54,0:22]=1; maps[15:50,45:58]=2
    source=tmp_path/'input.zip'
    with zipfile.ZipFile(source,'w') as archive:
        for frame in range(frames):
            data=BytesIO(); Image.fromarray(maps).save(data,'PNG')
            archive.writestr(f'labels/labels_{frame:05d}.png',data.getvalue())
    targets=[{'id':'one','reference_index':0,'anchor':{'x':.1,'y':.2,'time':0}},
             {'id':'two','reference_index':1,'anchor':{'x':.8,'y':.5,'time':0}}]
    assignments=[{'segment_id':t['id'],'target_id':t['id'],'start_frame':0,'end_frame':frames,'absent':False} for t in targets]
    choices=[{'segment_id':t['id'],'absent':False,'anchor':t['anchor']} for t in targets]
    job={'id':'quality-job','status':'preparing','tracking_applied_revision':1,
         'tracking_metadata':{'width':64,'height':64,'frames':frames,'duration':frames/24,
                              'anchor':{**targets[0]['anchor'],'frame':0},'scene_cuts':[]},
         'tracking_plan':{'version':2,'targets':targets,
                          'segments':[{**a,'id':a['segment_id'],'needs_review':True} for a in assignments]},
         'tracking_assignments':assignments,'tracking_choices':choices,
         'reviewed_masks_uri':'original', 'normalized_targets':[{**t,'scenes':[
             {'start':0,'end':frames/24,'absent':False,'anchor':t['anchor']}]} for t in targets],
         'generation_after_review_revision':1,'capacity_generation_authorized':True,
         'capacity_approval_revision':1,'generation_capacity_token':'old','review_generation_queued':True}
    class Records:
        data=deepcopy(job)
        def update(self,path,fn):self.data=fn(deepcopy(self.data));return deepcopy(self.data)
    class Cloud:
        downloads=0;uploads=0;objects={'original':source.read_bytes()}
        def download(self,uri,path,limit):self.downloads+=1;path.write_bytes(self.objects[uri])
        def upload(self,bucket,key,path,mime):
            self.uploads+=1;self.objects[key]=path.read_bytes();return key
    records,cloud=Records(),Cloud()
    return job,Pipeline(replace(Settings(),worker_bucket='workers'),records,cloud),records,cloud


def test_quality_check_clears_stale_approval_preserves_other_person_and_is_cached(tmp_path):
    job,pipeline,records,cloud=fixture_job(tmp_path)
    checked=pipeline.check_reviewed_mask_quality(job)
    assert checked['reviewed_masks_uri']!='original' and cloud.objects['original']
    assert checked['reviewed_mask_quality']['review_required']
    assert checked['generation_after_review_revision'] is None
    assert not checked['capacity_generation_authorized'] and not checked['review_generation_queued']
    assert checked['generation_capacity_token'] is None
    assert checked['normalized_targets'][0]['guide_anchor_untrusted']
    assert checked['normalized_targets'][1]==job['normalized_targets'][1]
    assert cloud.downloads==1 and cloud.uploads==1
    assert pipeline.check_reviewed_mask_quality(checked)==checked
    assert cloud.downloads==1 and cloud.uploads==1


def test_final_recovered_masks_are_checked_before_composition(monkeypatch):
    import swapmein.mask_recovery as recovery
    order=[]
    def recover(pipeline,job,remaining):order.append('recover');return {**job,'mask_recovery_completed':True}
    def check(self,job,remaining=None):
        assert job['mask_recovery_completed'];order.append('check');raise RuntimeError('stop-before-compose')
    monkeypatch.setattr(recovery,'recover_reviewed_masks',recover)
    monkeypatch.setattr(Pipeline,'check_reviewed_mask_quality',check)
    with pytest.raises(RuntimeError,match='stop-before-compose'):
        Pipeline(Settings(),None,None).rebuild_reviewed_guide({'id':'job','recover_missing_masks':True},lambda:60)
    assert order==['recover','check']
