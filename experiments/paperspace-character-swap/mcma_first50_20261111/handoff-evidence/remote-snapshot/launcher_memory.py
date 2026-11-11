"""Process-local RMS/RoPE plus MLP chunking, no installed-file changes."""
import runpy
import sys
from pathlib import Path
import torch
import comfy_kitchen as ck
from comfy_kitchen.backends.eager.rope import rms_rope_split_half_

main = Path('/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source/main.py')
sys.path.insert(0,str(main.parent))
import comfy.options
comfy.options.enable_args_parsing()
import comfy.ldm.minimax.model as minimax
import comfy.ops

saved_rope=ck.rms_rope_split_half_
saved_mlp=minimax.MLP.forward
seen=set()

def rope(q,k,freqs_cis,q_scale,k_scale=None,epsilon=1e-6,rot_dim=0):
    assert q.ndim==4 and k.shape==q.shape and freqs_cis.ndim==6
    assert freqs_cis.shape[1] in (1,q.shape[1]) and freqs_cis.shape[2]==1
    signature=('rope',str(q.dtype),tuple(q.shape),tuple(freqs_cis.shape),rot_dim)
    if signature not in seen:
        seen.add(signature);print('[REALISM_CHUNKED_V2] '+repr(signature)+' chunk=512',flush=True)
    for start in range(0,q.shape[1],512):
        end=min(start+512,q.shape[1])
        f=freqs_cis if freqs_cis.shape[1]==1 else freqs_cis[:,start:end]
        rms_rope_split_half_(q[:,start:end],k[:,start:end],f,q_scale,k_scale,epsilon=epsilon,rot_dim=rot_dim)
    return q,k

def mlp(self,x):
    assert x.ndim==2 and not torch.is_grad_enabled(), 'Only verified inference token layout'
    signature=('mlp',str(x.dtype),tuple(x.shape),self.fc1.out_features,self.fc2.out_features)
    if signature not in seen:
        seen.add(signature);print('[REALISM_CHUNKED_V2] '+repr(signature)+' chunk=4096',flush=True)
    result=torch.empty((x.shape[0],self.fc2.out_features),device=x.device,dtype=x.dtype)
    for start in range(0,x.shape[0],4096):
        end=min(start+4096,x.shape[0])
        result[start:end].copy_(comfy.ops.linear_input_act(self.fc2,self.fc1(x[start:end]),'swiglu'))
    return result

attention_patch=runpy.run_path(str(main.parent.parent/'attention_rows_patch.py'))
saved_attention=attention_patch['install'](minimax,2048)
sys.path.insert(0,str(Path(__file__).resolve().parent))
from long_attention import install as install_long_attention
install_long_attention(minimax,threshold=100000,query_chunk=4096,projection_chunk=2048)
print('[REALISM_ATTENTION_ROWS_V38] enabled2048',flush=True)
ck.rms_rope_split_half_=rope
minimax.MLP.forward=mlp
sys.argv[0]=str(main)
try:runpy.run_path(str(main),run_name='__main__')
finally:
    ck.rms_rope_split_half_=saved_rope
    minimax.MLP.forward=saved_mlp
    minimax.Attention.forward=saved_attention
