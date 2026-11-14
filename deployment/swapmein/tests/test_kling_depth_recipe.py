import json
from pathlib import Path
import pytest
from swapmein.provider import generation_payload
from swapmein.errors import PublicError
from swapmein.generation_models import generation_snapshot
from swapmein.media import Setup

def test_depth_prompt_matches_successful_render_exactly():
    proof=json.loads((Path(__file__).parent/'fixtures/kling_depth_recipe.json').read_text())
    payload=generation_payload(22,['one','two'],'guide',True,mask_style='identity_depth_multi',targets=proof['targets'],generation_model='kling-4-0',audio_processing=True,preserve_shots=True)
    assert payload['prompt']==proof['prompt']
    assert 'complete facial identity' not in payload['prompt']

@pytest.mark.parametrize('silent',[False,True])
def test_depth_keeps_audio_and_face_policy(silent):
    setup=Setup(trim=[0,15],generation_model='kling-4-0',anchor={'x':.4,'y':.5,'time':0},submit_without_audio=silent)
    saved=generation_snapshot(setup,kling_audio_processing=True,sam_provider='meta')
    assert saved['depth_guidance'] is True and saved['preparation_provider']=='replicate'
    assert saved['face_mesh'] is False and saved['audio_processing'] is (not silent)

def test_shared_depth_cannot_merge_two_selected_identities():
    targets=[{'anchor':{'x':x,'y':.5,'time':0},'reference_index':i} for i,x in enumerate([.2,.8])]
    with pytest.raises(PublicError,match='distinct-color'):
        generation_payload(15,['one','two'],'guide',True,mask_style='shared_depth_multi',targets=targets,generation_model='kling-4-0')

def test_single_depth_uses_coordinate_identity():
    payload=generation_payload(15,'one','guide',True,mask_style='shared_depth',anchor={'x':.4,'y':.5,'time':0},generation_model='kling-4-0')
    assert 'Replace the person at' in payload['prompt']
    assert 'All selected people share cyan' not in payload['prompt']
