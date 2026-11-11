"""Stage the latent-lock test on the render box. Creates ONLY new files.

Run from the remote experiment root after copying this folder's files next to it:
  /home/paperspace/ai/ComfyUI/.venv/bin/python stage_latentlock_v2.py
It does not start, stop or signal anything and does not edit any file the batch reads.
"""
from pathlib import Path
import json, py_compile, shutil, tempfile

P = Path(__file__).resolve().parent
SCRIPTS = ['latent_lock_nodes.py', 'offline_runner_latentlock_v2.py', 'queue_shot004_latentlock_v2.py', 'run_latentlock_v2_when_idle.py']
for name in SCRIPTS + ['plan-shot004-latentlock_v2.json', 'profile.json']:
    assert (P / name).is_file(), 'missing ' + name
with tempfile.TemporaryDirectory() as scratch:
    for name in SCRIPTS:
        py_compile.compile(str(P / name), cfile=str(Path(scratch) / (name + 'c')), doraise=True)

plan = json.loads((P / 'plan-shot004-latentlock_v2.json').read_text())
shot = plan['shots'][0]
assert plan['job_key'] == 'pretty_shot004_latentlock_v2' and len(plan['shots']) == 1
assert (shot['index'], shot['start_frame'], shot['end_frame'], shot['conditioning_frames'], shot['reference_indexes']) == (4, 676, 803, 141, [0, 1])
assert not (P / 'jobs' / plan['job_key']).exists(), 'trial directory already exists'

plugin = P / 'runtime/custom_nodes/pretty_latentlock_v2_nodes'
profile = P / 'profile-latentlock_v2.json'
assert not plugin.exists() and not profile.exists(), 'already staged'
base = json.loads((P / 'profile.json').read_text())
assert 'pretty_latentlock_v2_nodes' not in base['whitelist']
plugin.mkdir()
shutil.copy2(P / 'latent_lock_nodes.py', plugin / '__init__.py')
base['whitelist'] = base['whitelist'] + ['pretty_latentlock_v2_nodes']
profile.write_text(json.dumps(base, indent=2))
print(json.dumps({'staged': True, 'plugin': str(plugin), 'profile': str(profile),
                  'main_profile_changed': False, 'latent_lock': plan['latent_lock']}))
