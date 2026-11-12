from copy import deepcopy
from io import BytesIO
import hashlib
import json
import zipfile

import numpy as np
from PIL import Image
import pytest

from swapmein.errors import PublicError
from swapmein.mask_gap_tolerance import POLICY_VERSION, assess_mask_gaps
from swapmein.mask_recheck_plan import component_hash


def _write(path, images):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        for frame, image in enumerate(images):
            data = BytesIO();Image.fromarray(image.astype(np.uint16)).save(data, 'PNG')
            archive.writestr(f'labels/labels_{frame:05d}.png', data.getvalue())


def _case(tmp_path, *, frames=120, gap=None, minor=False):
    images=[]
    for _ in range(frames):
        a=np.zeros((100,100),np.uint16);a[20:60,20:60]=1
        if minor:a[70:80,80:90]=1
        images.append(a)
    checked=[image.copy()for image in images]
    if minor:
        for image in checked:image[70:80,80:90]=0
    if gap:
        for frame in range(*gap):checked[frame][:]=0
    anchor=dict(x=.3,y=.3,time=0.)
    target=dict(id='person',name='Person',reference_index=0,anchor=anchor)
    metadata=dict(width=100,height=100,frames=frames,duration=frames/24,anchor={**anchor,'frame':0},scene_cuts=[],targets=[target])
    section=dict(id='scene',target_id='person',reference_index=0,start_frame=0,end_frame=frames,needs_review=True)
    assignment={**section,'segment_id':'scene','selected_label':1,'absent':False};assignment.pop('id');assignment.pop('needs_review')
    job=dict(id='a'*32,tracking_metadata=metadata,tracking_plan=dict(version=2,targets=[target],segments=[section]),
        tracking_choices=[dict(segment_id='scene',absent=False,anchor=anchor)],tracking_assignments=[assignment],
        normalized_targets=[deepcopy(target)],tracking_applied_revision=1,tracking_revision=1,tracking_reviewed=True,
        identity_matching_completed=True,mask_identity_provenance='source_identity_match')
    raw=tmp_path/'raw.zip';clean=tmp_path/'checked.zip';_write(raw,images);_write(clean,checked)
    frame=gap[0]if gap else 20
    mask=(images[frame]==1)
    if minor:
        mask=np.zeros_like(mask);mask[70:80,80:90]=True
    candidate=dict(candidate_id='c'*32,target_id='person',label=1,segment_id='scene',start_frame=0,end_frame=frames,
        frame=frame,component_id=2 if minor else 1,component_sha256=component_hash(mask),
        box=[80,70,90,80]if minor else[20,20,60,60],reference_box=[20,20,60,60],
        anchor={'x':.85,'y':.75,'time':frame/24}if minor else{**anchor,'time':frame/24},reference_anchor=anchor,
        input_sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),review_revision=1,first_frame=frame,last_frame=frame)
    return job,raw,clean,[candidate],images,checked


def _assess(case):return assess_mask_gaps(*case[:4],review_revision=1)


def test_minor_untinted_limb_is_forgiven_without_restoring_any_pixel(tmp_path):
    case=_case(tmp_path,minor=True);job,raw,clean,candidates,_,_=case
    before=(raw.read_bytes(),clean.read_bytes(),deepcopy(job),deepcopy(candidates))
    report=_assess(case)
    assert report['policy_version']==POLICY_VERSION
    assert report['forgiven_candidate_ids']==['c'*32]and report['blocking_candidate_ids']==[]
    assert report['blocking_ranges']==[]
    assert report['tolerated_ranges']==[dict(target_id='person',label=1,segment_id='scene',start_frame=0,end_frame=120,
        kind='minor_untinted_component',reason='confirmed_main_component_retained')]
    assert report['assignments'][0]['clean_frames']==120
    assert report['assignments'][0]['anchor']['retained_share']==1
    assert (raw.read_bytes(),clean.read_bytes(),job,candidates)==before


@pytest.mark.parametrize('frames,gap',[(120,(90,110)),(720,(630,702)),(120,(102,120))])
def test_short_whole_tint_gap_needs_no_second_review_with_confirmed_same_shot_identity(tmp_path,frames,gap):
    case=_case(tmp_path,frames=frames,gap=gap);r=_assess(case)
    assert r['forgiven_candidate_ids']==['c'*32]and not r['blocking_candidate_ids']
    assert r['tolerated_ranges']==[dict(target_id='person',label=1,segment_id='scene',start_frame=gap[0],end_frame=gap[1],
        kind='brief_guide_gap',reason='confirmed_identity_video_inference')]
    assert r['assignments'][0]['maximum_gap_frames']==gap[1]-gap[0]
    assert not r['global_blockers']


def test_small_guide_gap_can_be_partial_and_still_never_restores_pixels(tmp_path):
    case=_case(tmp_path,gap=(90,110));job,raw,clean,candidates,images,checked=case
    for frame in range(90,110):checked[frame][20:25,20:25]=1
    _write(clean,checked);before=clean.read_bytes();r=_assess(case)
    assert r['forgiven_candidate_ids']==['c'*32]
    assert r['assignments'][0]['whole_mask_missing_ranges']==[]
    assert r['assignments'][0]['poor_guide_ranges']==[[90,110]]
    assert clean.read_bytes()==before


@pytest.mark.parametrize('change,reason',[
    ('unmatched','identity_not_confirmed'),('source_provenance_but_failed_match','identity_not_confirmed'),
    ('fallback','unreviewed_identity_fallback'),('pending_resolution','source_review_unresolved'),
    ('review_error','source_review_error'),('pending_sam_recovery','mask_recovery_pending'),
    ('revision','review_revision_mismatch'),('newer_review_revision','review_revision_mismatch'),
    ('pending_v4','pending_targeted_review'),('unknown_provenance','identity_not_confirmed')])
def test_unresolved_identity_recovery_and_existing_user_review_cannot_be_bypassed(tmp_path,change,reason):
    case=_case(tmp_path,minor=True);job=case[0]
    if change=='unmatched':job.update(identity_matching_completed=False,tracking_reviewed=False)
    elif change=='source_provenance_but_failed_match':job.update(identity_matching_completed=False)
    elif change=='fallback':job['matching_fallback_used']=True
    elif change=='pending_resolution':job['tracking_resolution_pending']=True
    elif change=='review_error':job['tracking_review_error']=True
    elif change=='pending_sam_recovery':job['recover_missing_masks']=True
    elif change=='revision':job['tracking_applied_revision']=2
    elif change=='newer_review_revision':job['tracking_revision']=2
    elif change=='pending_v4':job['targeted_mask_review']={'pending':True}
    elif change=='unknown_provenance':job['mask_identity_provenance']='unreviewed_guess'
    r=_assess(case)
    assert reason in r['global_blockers']
    assert r['forgiven_candidate_ids']==[]and r['blocking_candidate_ids']==['c'*32]
    assert r['tolerated_ranges']==[]


@pytest.mark.parametrize('provenance',[None,'manual_source_review'])
def test_complete_validated_manual_source_review_counts_as_identity_confirmation(tmp_path,provenance):
    case=_case(tmp_path,minor=True);job=case[0]
    job.update(identity_matching_completed=False,mask_identity_provenance=provenance,matching_fallback_used=True)
    r=_assess(case)
    assert r['forgiven_candidate_ids']==['c'*32]
    assert r['assignments'][0]['identity_provenance']=='manual_source_review'


@pytest.mark.parametrize('change,reason',[
    ('untrusted','identity_anchor_untrusted'),('coordinate','coordinate_fallback'),
    ('anchor_lost','source_anchor_not_retained'),('anchor_partial','source_anchor_not_retained'),
    ('long_gap','gap_too_long'),('clip_budget','clip_gap_budget_exceeded'),
    ('no_clean_evidence','insufficient_same_shot_guide'),('cross_cut','assignment_crosses_cut')])
def test_large_or_unanchored_losses_still_require_review(tmp_path,change,reason):
    case=_case(tmp_path,frames=720 if change == 'long_gap' else 120,minor=True)
    job,raw,clean,candidates,images,checked=case
    if change=='untrusted':job['normalized_targets'][0]['guide_anchor_untrusted']=True
    elif change=='coordinate':job['tracking_assignments'][0]['coordinate_fallback']=True;candidates.clear()
    elif change=='anchor_lost':checked[0][:]=0
    elif change=='anchor_partial':checked[0][30:60,20:60]=0
    elif change=='long_gap':
        for frame in range(400,473):checked[frame][:]=0
    elif change=='clip_budget':
        for frame in range(80,111):checked[frame][:]=0
    elif change=='no_clean_evidence':
        for frame in range(11,120):checked[frame][:]=0
    elif change=='cross_cut':job['tracking_metadata']['scene_cuts']=[2.0];candidates.clear()
    _write(clean,checked);r=_assess(case)
    assert reason in r['assignments'][0]['blocking_reasons']
    assert r['forgiven_candidate_ids']==[]and r['blocking_candidate_ids']==(['c'*32] if candidates else [])
    assert r['tolerated_ranges']==[]


@pytest.mark.parametrize('change',['changed_component_hash','wrong_target','wrong_bounds','stale_input_hash','stale_revision',
    'invented_kept_pixels','unknown_label','missing_choices','overlapping_sections','absent_has_pixels'])
def test_mask_and_source_review_bindings_are_validated_before_forgiveness(tmp_path,change):
    case=_case(tmp_path,minor=True);job,raw,clean,candidates,images,checked=case
    if change=='changed_component_hash':candidates[0]['component_sha256']='0'*64
    elif change=='wrong_target':candidates[0]['target_id']='someoneelse'
    elif change=='wrong_bounds':candidates[0]['start_frame']=1
    elif change=='stale_input_hash':candidates[0]['input_sha256']='0'*64
    elif change=='stale_revision':candidates[0]['review_revision']=2
    elif change=='invented_kept_pixels':checked[20][1,1]=1;_write(clean,checked)
    elif change=='unknown_label':images[20][1,1]=2;_write(raw,images)
    elif change=='missing_choices':job['tracking_choices']=[]
    elif change=='overlapping_sections':
        duplicate=deepcopy(job['tracking_assignments'][0]);duplicate['segment_id']='another'
        job['tracking_assignments'].append(duplicate)
    elif change=='absent_has_pixels':
        job['tracking_choices'][0].update(absent=True,anchor=None);job['tracking_assignments'][0]['absent']=True
    with pytest.raises(PublicError):_assess(case)


def test_sha_and_thresholds_bind_the_assessment_and_no_candidate_does_not_skip_gap_checks(tmp_path):
    case=_case(tmp_path,gap=(90,110));case[3].clear();r=_assess(case)
    assert r['input_sha256']==hashlib.sha256(case[1].read_bytes()).hexdigest()
    assert r['output_sha256']==hashlib.sha256(case[2].read_bytes()).hexdigest()
    assert r['tolerated_ranges']and r['assignments'][0]['eligible']
    assert r['thresholds']['max_gap_frames']==72
    assert r['thresholds']['max_clip_gap_share']==.25


def test_explicit_manual_provenance_is_valid_before_ready_ui_flag_is_set(tmp_path):
    case=_case(tmp_path,minor=True);job=case[0]
    job.update(identity_matching_completed=False,mask_identity_provenance='manual_source_review',tracking_reviewed=False)
    assert _assess(case)['forgiven_candidate_ids']==['c'*32]
    job['mask_identity_provenance']=None
    assert 'identity_not_confirmed'in _assess(case)['global_blockers']


def test_candidate_cannot_borrow_a_different_targets_component_even_with_a_valid_pixel_hash(tmp_path):
    case=_case(tmp_path,minor=True);job,raw,clean,candidates,images,checked=case
    target=deepcopy(job['tracking_plan']['targets'][0]);target.update(id='second',reference_index=1,anchor={'x':.85,'y':.85,'time':0.})
    job['tracking_metadata']['targets'].append(deepcopy(target));job['tracking_plan']['targets'].append(deepcopy(target));job['normalized_targets'].append(deepcopy(target))
    section=deepcopy(job['tracking_plan']['segments'][0]);section.update(id='second-scene',target_id='second',reference_index=1)
    job['tracking_plan']['segments'].append(section)
    assignment=deepcopy(job['tracking_assignments'][0]);assignment.update(segment_id='second-scene',target_id='second',selected_label=2,reference_index=1)
    job['tracking_assignments'].append(assignment);job['tracking_choices'].append(dict(segment_id='second-scene',absent=False,anchor=target['anchor']))
    for a,b in zip(images,checked):a[80:90,80:90]=2;b[80:90,80:90]=2
    _write(raw,images);_write(clean,checked)
    mask=images[20]==2;candidates[0].update(label=2,component_id=1,anchor={'x':.85,'y':.85,'time':20/24},
        component_sha256=component_hash(mask),box=[80,80,90,90],reference_box=[80,80,90,90],reference_anchor=target['anchor'],
        input_sha256=hashlib.sha256(raw.read_bytes()).hexdigest())
    with pytest.raises(PublicError,match='labels must match'):_assess(case)


def test_clean_evidence_in_another_shot_cannot_support_an_unanchored_gap(tmp_path):
    case=_case(tmp_path,gap=(100,120));job,raw,clean,candidates,_,_=case
    job['tracking_metadata']['scene_cuts']=[100/24]
    job['tracking_plan']['segments'][0]['end_frame']=100;job['tracking_assignments'][0]['end_frame']=100
    section=deepcopy(job['tracking_plan']['segments'][0]);section.update(id='next',start_frame=100,end_frame=120)
    assignment=deepcopy(job['tracking_assignments'][0]);assignment.update(segment_id='next',start_frame=100,end_frame=120)
    job['tracking_plan']['segments'].append(section);job['tracking_assignments'].append(assignment)
    job['tracking_choices'].append(dict(segment_id='next',absent=False,anchor={'x':.3,'y':.3,'time':100/24}))
    candidates[0].update(segment_id='next',start_frame=100,end_frame=120,reference_anchor={'x':.3,'y':.3,'time':100/24})
    r=_assess(case);second=r['assignments'][1]
    assert second['clean_frames']==0 and not second['eligible']
    assert 'insufficient_same_shot_guide'in second['blocking_reasons']
    assert 'source_anchor_not_retained'in second['blocking_reasons']
    assert r['forgiven_candidate_ids']==[]
