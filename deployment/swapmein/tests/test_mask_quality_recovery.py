from copy import deepcopy
from io import BytesIO
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.mask_quality_recovery import (identity_recovery_fields,
    resolve_identity_recovery, withhold_untrusted_masks)


def job():
    one = {'id':'one','name':'Person 1','reference_index':0,
           'anchor':{'x':.2,'y':.4,'time':0},'color':'cyan'}
    two = {'id':'two','name':'Person 2','reference_index':1,
           'anchor':{'x':.8,'y':.4,'time':0},'color':'magenta'}
    for target in [one,two]:
        target['scenes']=[{'start':0,'end':1,'absent':False,'anchor':deepcopy(target['anchor'])}]
    two['guide_anchor_untrusted']=True
    return {'id':'job','tracking_applied_revision':1,'tracking_revision':1,
        'tracking_metadata':{'width':16,'height':16,'frames':24,'duration':1,
            'anchor':{**one['anchor'],'frame':0},'scene_cuts':[.5]},
        'tracking_plan':{'targets':[deepcopy(one),deepcopy(two)]},
        'normalized_targets':[one,two], 'tracking_choices':[{'segment_id':'old'}],
        'tracking_assignments':[{'segment_id':'old'}], 'reviewed_mask_quality':{
            'sha256':None, 'suppressed_ranges':[{'target_id':'one','start_frame':1,'end_frame':2,'reason':'brief'},
            {'target_id':'two','start_frame':0,'end_frame':3,'reason':'uncertain'}]}}


def archive(path):
    frame=np.zeros((16,16),np.uint16);frame[1:5,1:5]=1;frame[1:5,10:14]=2
    with zipfile.ZipFile(path,'w') as z:
        for i in range(24):
            b=BytesIO();Image.fromarray(frame).save(b,'PNG');z.writestr(f'labels/labels_{i:05d}.png',b.getvalue())
    return frame


def choices(plan,absent=False):
    return [{'segment_id':s['id'],'absent':absent,
        'anchor':None if absent else {'x':.7,'y':.3,'time':s['seek_time']}}
        for s in plan['segments']]


def test_only_unsafe_target_tint_removed_and_original_masks_preserved(tmp_path):
    original=tmp_path/'input.zip';output=tmp_path/'clean.zip';frame=archive(original);before=original.read_bytes();j=job()
    report=withhold_untrusted_masks(original,output,j)
    assert original.read_bytes()==before
    with zipfile.ZipFile(output) as z:
        result=np.asarray(Image.open(BytesIO(z.read('labels/labels_00000.png'))))
    assert np.array_equal(result==1,frame==1) and not (result==2).any()
    assert report['coverage']==[16]*24
    assert report['identity_recovery_removed_pixels']==16*24
    assert [x for x in report['suppressed_ranges'] if x['target_id']=='two']==[{
        'target_id':'two','label':2,'start_frame':0,'end_frame':24,'reason':'identity_reselection',
        'coordinate_guidance':True,'absent':False}]
    with pytest.raises(PublicError):withhold_untrusted_masks(original,output,j)


def test_recovery_only_exposes_unsafe_target_and_clears_stale_submissions():
    j=job();old=deepcopy(j);fields=identity_recovery_fields(j)
    assert j==old and fields['status']=='tracking_review' and fields['tracking_revision']==2
    assert [t['id'] for t in fields['tracking_plan']['targets']]==['two']
    assert fields['tracking_choices']==fields['tracking_assignments']==[]
    assert not fields['review_generation_queued'] and fields['generation_capacity_token'] is None
    assert fields['generation_after_review_revision'] is None
    assert 'scenes' not in fields['tracking_plan']['targets'][0]


def test_confirmed_coordinates_merge_in_original_order_and_do_not_loop():
    j=job();j.update(identity_recovery_fields(j));j['reviewed_mask_quality']['suppressed_ranges']=[{
        'target_id':'two','start_frame':0,'end_frame':24,'reason':'identity_reselection'}]
    safe=deepcopy(j['normalized_targets'][0]);resolved=resolve_identity_recovery(j,choices(j['tracking_plan']))
    assert [t['id'] for t in resolved]==['one','two'] and resolved[0]==safe
    assert resolved[1]['anchor']=={'x':.7,'y':.3,'time':0}
    assert not resolved[1].get('guide_anchor_untrusted')
    assert all(not s.get('guide_anchor_untrusted') and s['coordinate_fallback'] and s['guide_mask_suppressed_ranges'] for s in resolved[1]['scenes'])
    assert identity_recovery_fields({**j,'normalized_targets':resolved}) is None


def test_unsafe_target_can_be_absent_when_other_target_visible():
    j=job();j.update(identity_recovery_fields(j))
    resolved=resolve_identity_recovery(j,choices(j['tracking_plan'],absent=True))
    assert all(s['absent'] for s in resolved[1]['scenes'])
    assert resolved[0]==j['normalized_targets'][0]


def test_recovery_cannot_change_safe_targets_or_forge_plan():
    j=job();j.update(identity_recovery_fields(j));bad=choices(j['tracking_plan']);bad[0]['segment_id']='safe-person'
    with pytest.raises(PublicError):resolve_identity_recovery(j,bad)
    j['tracking_plan']['segments'][0]['needs_review']=False
    with pytest.raises(PublicError):resolve_identity_recovery(j,choices(j['tracking_plan']))


def test_legacy_cached_quality_report_rechecks_until_unsafe_tint_fully_withheld(tmp_path):
    from test_mask_quality_pipeline import fixture_job
    from swapmein.mask_quality import POLICY_VERSION, _sha
    original_job, pipeline, records, cloud = fixture_job(tmp_path)
    original_job['normalized_targets'][0]['guide_anchor_untrusted'] = True
    original_job['reviewed_mask_quality'] = {
        'policy_version': POLICY_VERSION, 'review_revision': 1, 'output_uri': 'original',
        'sha256': _sha(tmp_path/'input.zip'), 'review_required': True,
        'suppressed_ranges': [{'target_id':'one','start_frame':0,'end_frame':3,'reason':'legacy_uncertain'}]}
    original = cloud.objects['original']
    checked = pipeline.check_reviewed_mask_quality(original_job)
    assert cloud.downloads == 1 and cloud.uploads == 1
    assert checked['reviewed_mask_quality']['identity_recovery_target_ids'] == ['one']
    assert cloud.objects['original'] == original
    with zipfile.ZipFile(BytesIO(cloud.objects[checked['reviewed_masks_uri']])) as archive:
        pixels = np.asarray(Image.open(BytesIO(archive.read('labels/labels_00000.png'))))
    with zipfile.ZipFile(BytesIO(original)) as archive:
        before = np.asarray(Image.open(BytesIO(archive.read('labels/labels_00000.png'))))
    assert not (pixels == 1).any() and np.array_equal(pixels == 2, before == 2)
    assert pipeline.check_reviewed_mask_quality(checked) == checked
    assert cloud.downloads == 1
