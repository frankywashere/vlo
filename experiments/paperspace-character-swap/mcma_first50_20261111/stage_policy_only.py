from pathlib import Path
import shutil,json,os
P=Path(__file__).resolve().parent;src=P/'jobs/mcma_temporal_bind_pilot_v1';d=P/'jobs/mcma_policy_only_pilot_v1';d.mkdir(exist_ok=True)
for name in ['source.mp4','reference-0.image','reference-1.image','reference-2.image','edit-mask-001.npz']:
 if not (d/name).exists():os.link(src/name,d/name)
m=json.loads((src/'job.json').read_text());m['job_id']=d.name;(d/'job.json').write_text(json.dumps(m,indent=2))
n=P/'runtime/custom_nodes/mcma_policy_only_nodes'
if not n.exists():shutil.copytree(P/'runtime/custom_nodes/mcma_temporal_bind_nodes',n)
shutil.copy2(P/'spatial_nodes_policy_only.py',n/'spatial.py');shutil.copy2(P/'policy_only_native_temporal.py',n/'minimax_h3_native_temporal.py')
profile=json.loads((P/'profile-temporal-bind-pilot.json').read_text());profile['whitelist']=['mcma_policy_only_nodes' if x=='mcma_temporal_bind_nodes' else x for x in profile['whitelist']];(P/'profile-policy-only-pilot.json').write_text(json.dumps(profile,indent=2))
print('isolated policy-only candidate staged')
