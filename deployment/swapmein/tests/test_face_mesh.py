"""Selected-only CPU mesh guidance, immutable policy and source-RGB proof."""
import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import numpy as np
import pytest

from swapmein.media import Setup
from swapmein.pipeline_settings import face_mesh_snapshot
from swapmein.provider import generation_payload

PATCH = Path(__file__).parents[1] / 'worker_patch' / 'face_mesh.py'


def load_module():
    spec=importlib.util.spec_from_file_location('tested_face_mesh', PATCH)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def face(x, y):
    return [NS(x=x, y=y), NS(x=x+.04, y=y+.02)]


@pytest.mark.parametrize('masked', [False, True])
@pytest.mark.parametrize('default', [False, True])
@pytest.mark.parametrize('visible', [False, True])
@pytest.mark.parametrize('override', [None, False, True])
def test_face_mesh_default_or_visible_override(masked, default, visible, override):
    defaults={'face_mesh_masked_default':default if masked else not default,
              'face_mesh_unmasked_default':default if not masked else not default,
              'face_mesh_user_control':visible}
    setup=Setup(trim=[0,4], anchor={'x':.5,'y':.5,'time':0}, enhanced_character_masking=masked, face_mesh=override)
    expected=override if visible and override is not None else default
    assert face_mesh_snapshot(defaults,setup)=={'face_mesh':expected}


def test_association_rejects_background_and_incomplete_head_without_guessing():
    module=load_module();labels=np.zeros((100,100),np.uint16)
    labels[10:40,10:40]=7;labels[60:90,60:90]=9
    assert module.face_label(face(.2,.2),labels,{7})==7
    assert module.face_label(face(.7,.7),labels,{7}) is None
    assert module.face_label(face(.5,.5),labels,{7}) is None
    assert module.face_label([NS(x=float('nan'),y=.2)],labels,{7}) is None


@pytest.mark.parametrize('all_people', [False, True])
def test_mesh_draws_only_selected_masks_and_rejects_ambiguous_identity(monkeypatch, all_people):
    module=load_module()
    faces=[face(.2,.2),face(.25,.25),face(.7,.7)]
    inputs=[]
    detector=NS(detect=lambda image: (inputs.append(image.data.copy()) or NS(face_landmarks=faces)),close=lambda:None)
    connection=[NS(start=0,end=1)]
    connections=NS(**{name:connection for name in ['FACE_LANDMARKS_TESSELATION','FACE_LANDMARKS_LIPS','FACE_LANDMARKS_LEFT_EYE','FACE_LANDMARKS_RIGHT_EYE']})
    class Base:
        Delegate=NS(CPU='CPU')
        def __init__(self,**kwargs):assert kwargs['delegate']=='CPU'
    mp=NS(Image=lambda **kwargs:NS(**kwargs),ImageFormat=NS(SRGB='rgb'),tasks=NS(BaseOptions=Base,
        vision=NS(FaceLandmarkerOptions=lambda **kwargs:NS(**kwargs),RunningMode=NS(IMAGE='image'),
                  FaceLandmarker=NS(create_from_options=lambda _:detector),FaceLandmarksConnections=connections)))
    monkeypatch.setitem(sys.modules,'mediapipe',mp)
    painter=module.FaceMeshPainter(model='local-model',multiple_per_label=all_people)
    labels=np.zeros((100,100),np.uint16);labels[10:40,10:40]=7;labels[60:90,60:90]=9
    target=labels==7
    original=np.full((100,100,3),(200,25,15),np.uint8)
    guide=original.copy();guide[target]=(30,30,30)
    before=guide.copy();painter.apply(original,guide,labels,target);painter.close()
    assert np.array_equal(inputs[0],original)  # Detector sees RGB, never the guide/depth.
    assert np.array_equal(guide[~target],before[~target])
    assert painter.metrics['unmatched_faces']==1
    assert painter.metrics['faces_drawn']==(2 if all_people else 0)
    assert bool(np.any(guide[target]!=before[target])) is all_people


def test_mesh_prompt_keeps_audio_and_mask_rules_and_removes_mesh_lines():
    prompt=generation_payload(5,'image','video',True,mask_style='depth',face_mesh=True)['prompt']
    assert 'remove every mesh line' in prompt and 'mouth-motion guidance' in prompt
    assert 'colored depth imagery' in prompt and 'mouth synchronization' in prompt
