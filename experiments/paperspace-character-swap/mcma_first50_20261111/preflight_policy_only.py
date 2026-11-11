from pathlib import Path
import sys,json,inspect,torch
P=Path(__file__).resolve().parent
sys.path.insert(0,'/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source');sys.path.insert(0,str(P/'runtime/custom_nodes'))
import mcma_policy_only_nodes as package
from mcma_policy_only_nodes import minimax_h3_native_temporal as temporal
from comfy.ldm.minimax.model import PackedLayout
class MiniMaxH3Model:pass
class Fake:
 def __init__(self):self.model_options={}
 def get_model_object(self,name):assert name=='diffusion_model';return MiniMaxH3Model()
 def clone(self):x=Fake();x.model_options=dict(self.model_options);return x
 def set_model_unet_function_wrapper(self,fn):self.model_options['model_function_wrapper']=fn
refs=[dict(kind='image',latent_h=4,latent_w=4) for _ in range(3)]+[dict(kind='video_audio',latent_t=2,latent_h=4,latent_w=4,ref_audio_t=3)]
native=PackedLayout(5,2,4,4,3,refs=refs);before=native.position_ids.clone();patched=temporal.patch_temporal_model(Fake());evidence={}
def model_fn(x,t,**c):
 assert c['minimax_payload']['layout'] is native
 hook=c['transformer_options']['optimized_attention_override'];ctx=inspect.getclosurevars(hook).nonlocals['ctx']
 assert torch.equal(torch.unique(ctx.times[ctx.source]),torch.unique(ctx.times[ctx.target]))
 evidence.update(native_rope_layout_retained=True,time_attention_context_aligned=True)
 return x
args={'input':torch.ones(1),'timestep':torch.tensor([.5]),'c':{'minimax_payload':{'layout':native,'refs':refs,'keyframes':None},'transformer_options':{}}}
patched.model_options['model_function_wrapper'](model_fn,args)
assert torch.equal(native.position_ids,before);json.dumps({k:v.INPUT_TYPES() for k,v in package.NODE_CLASS_MAPPINGS.items()})
evidence.update(native_layout_not_mutated=True,registration_json_ok=True,quality_proven=False)
(P/'policy-only-preflight.json').write_text(json.dumps(evidence,indent=2));print(json.dumps(evidence))
