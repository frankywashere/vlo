from pathlib import Path
import sys,os,importlib.util,json
P=Path(__file__).resolve().parent
sys.path.insert(0,'/home/paperspace/ai/recastfx_character_swap_short_20261103_v1/source')
import torch,numpy as np,comfy.nested_tensor
spec=importlib.util.spec_from_file_location('mesh_nodes',P/'spatial_nodes_mesh_pilot.py');mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
os.environ['RECASTFX_WORKER_SPOOL']=str(P/'jobs')
spec=importlib.util.spec_from_file_location('cuts',P/'runtime/custom_nodes/pretty_continuity_nodes/__init__.py');cuts=importlib.util.module_from_spec(spec);spec.loader.exec_module(cuts)
images=cuts.PrettyCutSource().load('mcma_mesh_pilot_v1',0,49,56)[0];before=images.clone();out=mod.McmaSourceFaceMesh().draw(images,'mcma_mesh_pilot_v1')[0]
assert torch.equal(images,before);assert torch.equal(out[:, :, :7],images[:, :, :7]);assert len(out)==56
from PIL import Image
for i in [0,8,16,24,32,40,48]:Image.fromarray((out[i,:,7:1081].numpy()*255).astype(np.uint8)).save(P/f'mesh-reviewed-{i:03d}.jpg')
video=torch.zeros((1,24,17,48,68));audio=torch.zeros((1,8,17));original=torch.ones_like(video);latent={'samples':comfy.nested_tensor.NestedTensor((video,audio))};positive=[[torch.zeros(1),{'minimax_refs':[{'kind':'video_audio','latent':video}]}]]
locked=mod.PrettySourceLockedLatent().lock(positive,{'samples':original},latent,'mcma_mesh_pilot_v1',56,0,.5,1,1)[0]
assert torch.equal(locked['samples'].unbind()[0],original);assert locked['noise_mask'].shape==(1,1,17,48,68)
(P/'mesh-dryrun.json').write_text(json.dumps({'raw_images_preserved':True,'padding_preserved':True,'source_latent_uses_raw_video':True,'mask_shape':list(locked['noise_mask'].shape),'frames':56,'output_frames':49},indent=2));print('mesh node and original source latent checks passed')
