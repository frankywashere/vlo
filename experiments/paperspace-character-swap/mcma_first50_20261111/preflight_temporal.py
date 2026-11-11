from pathlib import Path
import sys,torch,json,urllib.request
P=Path(__file__).resolve().parent
for port in [8192,8199,8200]:
 with urllib.request.urlopen(f'http://127.0.0.1:{port}/queue',timeout=5) as r:q=json.load(r)
 assert not q.get('queue_running') and not q.get('queue_pending'),('Shared work active',port)
assert not Path('/home/paperspace/ai/recastfx_site_worker_20261104_v1/jobs/active.json').exists()
sys.path.insert(0,'/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source');sys.path.insert(0,str(P/'runtime/custom_nodes/mcma_temporal_nodes'))
from comfy.ldm.minimax.model import PackedLayout
import minimax_h3_native_temporal as temporal
import minimax_h3_temporal_fast as fast
refs=[dict(kind='image',latent_h=4,latent_w=4),dict(kind='video_audio',latent_t=2,latent_h=4,latent_w=4,ref_audio_t=3)]
native=PackedLayout(5,2,4,4,3,refs=refs);positions=native.position_ids.clone();layout,source,target=temporal.temporal_layout(native,refs,None,1);assert torch.equal(native.position_ids,positions)
plan=fast.make_plan(layout.position_ids,source,target,2,.2,.25,6)
q,k,v=[torch.randn((1,2,native.seq_len,128),device='cuda:0',dtype=torch.bfloat16) for _ in range(3)]
with torch.inference_mode():
 out=fast.attention(q,k,v,plan)
 times=layout.position_ids[:,0].to('cuda:0').float()/40;penalty=(2*(torch.relu((times[:,None]-times[None,:]).abs()-.2)/.25).square()).clamp(max=6)
 bias=torch.zeros_like(penalty);where=target.to('cuda:0')[:,None]&source.to('cuda:0')[None,:];bias[where]=-penalty[where]
 expected=torch.nn.functional.scaled_dot_product_attention(q.float(),k.float(),v.float(),attn_mask=bias[None,None]).to(out.dtype)
 error=float((out-expected).abs().max());assert error<.04,error
result={'import_ok':True,'native_packed_layout_api':True,'paired_audio_layout':True,'layout_not_mutated':True,'flash_lse_kernel_ran':True,'tiny_attention_max_absolute_error_vs_fp32_sdpa':error,'full_model_compatibility_proven':False,'character_swap_quality_proven':False,'shared_runtime_unchanged':True}
(P/'temporal-preflight.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
