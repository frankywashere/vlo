from io import BytesIO

import pytest

from swapmein.errors import PublicError
import zipfile

import numpy as np
from PIL import Image

from swapmein.mask_quality import sanitize_reviewed_masks


def run(tmp_path, maps, anchor=(.3, .35, 0), *, cuts=None, name='safe.zip', absent=()):
    archive = tmp_path / 'input.zip'
    with zipfile.ZipFile(archive, 'w') as out:
        for frame, labels in enumerate(maps):
            data = BytesIO(); Image.fromarray(labels).save(data, 'PNG')
            out.writestr(f'labels/labels_{frame:05d}.png', data.getvalue())
    h, w = maps[0].shape; frames = len(maps)
    targets = [{'id': 'one', 'anchor': dict(x=anchor[0],y=anchor[1],time=anchor[2]/24)},
               {'id': 'two', 'anchor': dict(x=.8,y=.5,time=0)}]
    metadata = dict(width=w,height=h,frames=frames,duration=frames/24,
                    anchor=dict(x=anchor[0],y=anchor[1],time=anchor[2]/24,frame=anchor[2]),scene_cuts=cuts or [])
    boundaries = [0,*[round(x*24) for x in cuts or []],frames]
    assignments=[]; choices=[]
    for i,(start,end) in enumerate(zip(boundaries,boundaries[1:])):
        for t in targets:
            a=dict(t['anchor']);a['time']=max(start,min(end-1,anchor[2]))/24
            key=f'{i}-{t["id"]}'
            assignments.append(dict(segment_id=key,target_id=t['id'],start_frame=start,end_frame=end,absent=t['id'] in absent))
            choices.append(dict(segment_id=key,absent=t['id'] in absent,anchor=None if t['id'] in absent else a))
    output=tmp_path/name
    plan={'targets':targets,'segments':[{**a,'id':a['segment_id'],'needs_review':True} for a in assignments]}
    report=sanitize_reviewed_masks(archive,metadata,plan,choices,assignments,output,review_revision=2)
    with zipfile.ZipFile(output) as out:
        result=[np.asarray(Image.open(BytesIO(out.read(f'labels/labels_{i:05d}.png')))) for i in range(frames)]
    return report,result,output


def base():
    mask=np.zeros((64,64),np.uint16);mask[10:45,10:28]=1;mask[15:50,45:58]=2
    return mask


def test_valid_motion_and_other_target_unchanged(tmp_path):
    maps=[]
    for f in range(5):
        a=base();a[a==1]=0;a[10:45,10+f:28+f]=1;maps.append(a)
    report,result,_=run(tmp_path,maps)
    assert not report['suppressed_ranges']
    assert all(np.array_equal(a,b) for a,b in zip(maps,result))


def test_disconnected_spill_removed_without_touching_other_person(tmp_path):
    maps=[base() for _ in range(5)]
    for a in maps:a[52:60,5:15]=1
    report,result,_=run(tmp_path,maps)
    assert report['component_removed_pixels']==400
    assert not report['suppressed_ranges']
    assert all(not a[52:60,5:15].any() for a in result)
    assert all(np.array_equal(a==2,b==2) for a,b in zip(maps,result))


def test_merged_jump_suppresses_only_target_not_infers_absent(tmp_path):
    maps=[base() for _ in range(5)]
    maps[3][3:60,2:43]=1;maps[4][3:60,2:43]=1
    report,result,_=run(tmp_path,maps)
    assert any(r['reason']=='merged_component_jump' for r in report['suppressed_ranges'])
    assert not (result[3]==1).any()
    assert np.array_equal(result[3]==2,maps[3]==2)
    assert all(r['coordinate_guidance'] and not r['absent'] for r in report['suppressed_ranges'])


def test_short_occlusion_retains_returning_component(tmp_path):
    maps=[base() for _ in range(5)];maps[2][maps[2]==1]=0
    report,result,_=run(tmp_path,maps)
    assert np.array_equal(result[3],maps[3])
    assert report['suppressed_ranges'][0]['start_frame']==2


def test_border_anchor_with_second_substantial_island_is_ambiguous(tmp_path):
    maps=[base() for _ in range(3)]
    for a in maps:
        a[a==1]=0;a[4:24,0:24]=1;a[34:54,0:22]=1
    report,result,_=run(tmp_path,maps,anchor=(.1,.2,0))
    assert report['suppressed_ranges'][0]['reason']=='ambiguous_border_components'
    assert all(not (a==1).any() for a in result)
    assert all(np.array_equal(a==2,b==2) for a,b in zip(maps,result))


def test_cut_resets_trajectory(tmp_path):
    maps=[base() for _ in range(4)]
    for a in maps[2:]:a[a==1]=0;a[4:60,4:35]=1
    report,result,_=run(tmp_path,maps,cuts=[2/24])
    assert all(np.array_equal(a,b) for a,b in zip(maps,result))
    assert not report['suppressed_ranges']


def test_deterministic_archive_and_report(tmp_path):
    maps=[base(),base()]
    a,_,p=run(tmp_path,maps,name='a.zip');b,_,q=run(tmp_path,maps,name='b.zip')
    assert a==b
    assert p.read_bytes()==q.read_bytes()


def test_supported_disconnected_body_at_review_anchor_is_retained(tmp_path):
    maps=[base() for _ in range(4)]
    for a in maps:
        a[a==1]=0
        a[10:20,10:28]=1
        a[10:45,10:18]=1
        a[25:50,22:28]=1
    report,result,_=run(tmp_path,maps,anchor=(.2,.25,0))
    assert all(np.array_equal(a,b) for a,b in zip(maps,result))
    assert report['removed_pixels']==0


def test_explicit_absence_unchanged_and_does_not_become_presence(tmp_path):
    maps=[base() for _ in range(3)]
    for a in maps:a[a==2]=0
    report,result,_=run(tmp_path,maps,absent=('two',))
    assert all(np.array_equal(a,b) for a,b in zip(maps,result))
    assert not report['suppressed_ranges']
    assert not report['review_required']


def state(tmp_path):
    run(tmp_path,[base(),base()])
    metadata=dict(width=64,height=64,frames=2,duration=2/24,anchor=dict(x=.3,y=.35,time=0,frame=0),scene_cuts=[])
    targets=[dict(id='one',anchor=dict(x=.3,y=.35,time=0)),dict(id='two',anchor=dict(x=.8,y=.5,time=0))]
    assignments=[dict(segment_id=t['id'],target_id=t['id'],start_frame=0,end_frame=2,absent=False) for t in targets]
    choices=[dict(segment_id=t['id'],anchor=dict(t['anchor']),absent=False) for t in targets]
    plan=dict(targets=targets,segments=[dict(**a,id=a['segment_id'],needs_review=True) for a in assignments])
    return tmp_path/'input.zip',metadata,plan,choices,assignments


@pytest.mark.parametrize('case', ['duplicate_target','unknown_target','duplicate_assignment','duplicate_choice',
    'missing_assignment','missing_choice','oversized_choices','gap','overlap','nan_anchor','outside_anchor',
    'outside_time','assignment_absence','bad_target_index','bad_revision'])
def test_corrupt_saved_state_rejected_before_output_writes(tmp_path,case):
    archive,metadata,plan,choices,assignments=state(tmp_path)
    revision=2
    if case=='duplicate_target':plan['targets'][1]['id']='one'
    elif case=='unknown_target':assignments[0]['target_id']='stranger'
    elif case=='duplicate_assignment':assignments.append(dict(assignments[0]))
    elif case=='duplicate_choice':choices.append(dict(choices[0]))
    elif case=='missing_assignment':assignments.pop()
    elif case=='missing_choice':choices.pop()
    elif case=='oversized_choices':choices*=151
    elif case=='gap':assignments[0]['end_frame']=1;plan['segments'][0]['end_frame']=1
    elif case=='overlap':
        a={**assignments[0],'segment_id':'overlap'};assignments.append(a)
        plan['segments'].append({**a,'id':'overlap','needs_review':True});choices.append({**choices[0],'segment_id':'overlap'})
    elif case=='nan_anchor':choices[0]['anchor']['x']=float('nan')
    elif case=='outside_anchor':choices[0]['anchor']['y']=1.1
    elif case=='outside_time':choices[0]['anchor']['time']=2/24
    elif case=='assignment_absence':assignments[0]['absent']=True
    elif case=='bad_target_index':plan['targets'][0]['label']=99
    elif case=='bad_revision':revision=-1
    out=tmp_path/'preserved.zip';out.write_bytes(b'preserve-existing-output')
    original=archive.read_bytes()
    with pytest.raises(PublicError):sanitize_reviewed_masks(archive,metadata,plan,choices,assignments,out,review_revision=revision)
    assert out.read_bytes()==b'preserve-existing-output'
    assert archive.read_bytes()==original
    assert not (tmp_path/'preserved.zip.partial').exists()


def test_unknown_categorical_label_and_absent_pixels_rejected(tmp_path):
    archive,metadata,plan,choices,assignments=state(tmp_path)
    out=tmp_path/'checked.zip'
    with zipfile.ZipFile(archive,'w') as z:
        for frame in range(2):
            a=base();a[0,0]=3;image=BytesIO();Image.fromarray(a).save(image,'PNG')
            z.writestr(f'labels/labels_{frame:05d}.png',image.getvalue())
    with pytest.raises(PublicError,match='unknown target label'):
        sanitize_reviewed_masks(archive,metadata,plan,choices,assignments,out)
    archive,metadata,plan,choices,assignments=state(tmp_path)
    assignments[0]['absent']=True;choices[0].update(absent=True,anchor=None)
    with pytest.raises(PublicError,match='explicitly absent'):
        sanitize_reviewed_masks(archive,metadata,plan,choices,assignments,out)
    assert not out.exists()


@pytest.mark.parametrize('alias',['output','partial'])
def test_output_alias_and_partial_cannot_overwrite_source(tmp_path,alias):
    import os
    archive,metadata,plan,choices,assignments=state(tmp_path)
    out=tmp_path/'alias.zip';victim=out if alias=='output' else tmp_path/'alias.zip.partial'
    os.link(archive,victim)
    original=archive.read_bytes()
    with pytest.raises(PublicError):sanitize_reviewed_masks(archive,metadata,plan,choices,assignments,out)
    assert archive.read_bytes()==original
    assert victim.read_bytes()==original
