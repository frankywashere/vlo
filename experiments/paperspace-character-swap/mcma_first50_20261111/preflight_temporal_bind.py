from pathlib import Path
import sys,json,torch
P=Path(__file__).resolve().parent
sys.path.insert(0,'/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source');sys.path.insert(0,str(P/'runtime/custom_nodes'))
import mcma_temporal_bind_nodes as nodes
from mcma_temporal_bind_nodes import minimax_h3_reference_binding as binding,minimax_h3_native_temporal as temporal,minimax_h3_temporal_fast as fast
from comfy.ldm.minimax.model import PackedLayout
assert all(isinstance(k,str) for k in nodes.NODE_CLASS_MAPPINGS);json.dumps({k:v.INPUT_TYPES() for k,v in nodes.NODE_CLASS_MAPPINGS.items()})
male,female=nodes.NODE_CLASS_MAPPINGS['McmaPersonBindingMasks']().load('mcma_temporal_bind_pilot_v1');assert male.shape==female.shape==(56,768,1088);assert not ((male>0)&(female>0)).any()
class Fake:
 def __init__(self):self.model_options={'bfs_h3_native_temporal_v1':True}
 def clone(self):x=Fake();x.model_options=dict(self.model_options);return x
base=Fake();model=base
for picture,mask in [(1,male),(2,male),(3,female)]:model=binding.BFSMiniMaxH3ReferenceBinding().apply(model,picture_index=picture,reference_strength=4.,other_reference_strength=1.,target_mask=mask)[0]
assert 'transformer_options' not in base.model_options
refs=[dict(kind='image',latent_h=4,latent_w=4) for _ in range(3)]+[dict(kind='video_audio',latent_t=17,latent_h=4,latent_w=4,ref_audio_t=4)]
native=PackedLayout(5,17,4,4,4,refs=refs);layout,src,tgt=temporal.temporal_layout(native,refs,None,1);plan=fast.make_plan(layout.position_ids,src,tgt,2.,.2,.25,6.)
controls=model.model_options['transformer_options'][binding.OPTION];bias=binding.build_biases(layout,refs,plan,controls,'cpu');assert len(bias)==3 and torch.equal(bias[0][2],bias[1][2]);assert all(torch.isfinite(x[2]).all() and x[2].min()>=0 and x[2].max()<=1.3864 for x in bias)
report={'registration_json_ok':True,'male_female_mask_disjoint':True,'male_photo_biases_equal':True,'reference_controls_do_not_mutate_base_model':True,'paired_audio_layout_ok':True,'biases_finite_and_bounded':True,'quality_proven':False}
(P/'temporal-bind-preflight.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))
