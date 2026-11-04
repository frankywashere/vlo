"""Pinned GCE bootstrap: reuse installed models; fetch only verified small releases.

The controller owns VM creation/start/idle-stop. This code never provisions,
copies, installs packages, or downloads models. The startup wrapper shuts down
the guest if bootstrap or its foreground worker exits.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.parse
import urllib.request

ROOT=Path('/opt/recastfx/site-worker-20261104-v1')
PYTHON=Path('/opt/recastfx/venv-expanded/bin/python')
SOURCE=Path('/opt/recastfx/software/ComfyUI-Omni-PDD')
MODELS=Path('/opt/recastfx/models')
FILES={
 'model':'diffusion_models/minimax_h3_ref2va_pruned_bf16.safetensors',
 'text':'text_encoders/qwen3vl_32b_minimax_h3_bf16.safetensors',
 'vae':'vae/minimax_h3_video_vae_fp16.safetensors',
 'audio_vae':'vae/minimax_h3_audio_vae_fp32.safetensors',
 'turbo':'loras/minimax_h3_fl2v_turbo_8step_v1.0_768p_comfyui_bf16.safetensors',
 'character':'loras/h3_character_swap_v1.5.safetensors',
}
HEX=re.compile(r'[0-9a-f]{64}')
METADATA='http://169.254.169.254/computeMetadata/v1/'

def metadata(path: str) -> str:
 request=urllib.request.Request(METADATA+path,headers={'Metadata-Flavor':'Google'})
 with urllib.request.urlopen(request,timeout=10) as response:return response.read(262144).decode().strip()

def access_token() -> str:
 return json.loads(metadata('instance/service-accounts/default/token'))['access_token']

def gcs_url(uri: str) -> str:
 parsed=urllib.parse.urlsplit(uri)
 if parsed.scheme!='gs' or not parsed.netloc or not parsed.path.strip('/') or parsed.query or parsed.fragment or parsed.username or parsed.password:
  raise ValueError('An immutable GCS artifact URI is required')
 return 'https://storage.googleapis.com/storage/v1/b/'+urllib.parse.quote(parsed.netloc,safe='')+'/o/'+urllib.parse.quote(parsed.path.lstrip('/'),safe='')+'?alt=media'

def authenticated_bytes(url: str,limit: int) -> bytes:
 request=urllib.request.Request(url,headers={'Authorization':'Bearer '+access_token()})
 with urllib.request.urlopen(request,timeout=90) as response:data=response.read(limit+1)
 if len(data)>limit:raise ValueError('The worker artifact exceeds its bounded size')
 return data

def verified_download(uri: str,expected: str,limit: int) -> bytes:
 if not HEX.fullmatch(expected):raise ValueError('A SHA256 artifact pin is required')
 data=authenticated_bytes(gcs_url(uri),limit)
 if hashlib.sha256(data).hexdigest()!=expected:raise ValueError('The worker artifact hash did not match')
 return data

def extract_release(data: bytes,destination: Path) -> None:
 """Only small regular files/directories: no links, devices or path traversal."""
 destination=destination.resolve()
 with tarfile.open(fileobj=io.BytesIO(data),mode='r:gz') as archive:
  members=archive.getmembers()
  if len(members)>1000 or sum(x.size for x in members)>64*1024*1024:raise ValueError('Expanded worker release is too large')
  seen=set()
  for member in members:
   path=PurePosixPath(member.name)
   if path.is_absolute() or '..' in path.parts or not path.parts or member.name in seen or not(member.isdir() or member.isfile()):raise ValueError('Unsafe worker release entry')
   if path.parts[0]!='self_hosted_worker' or not (destination/str(path)).resolve().is_relative_to(destination):raise ValueError('Unexpected worker release layout')
   seen.add(member.name)
  if 'self_hosted_worker/runner.py' not in seen:raise ValueError('The worker entrypoint is absent')
  for member in members:
   target=destination/member.name
   if member.isdir():target.mkdir(parents=True,exist_ok=True,mode=0o700)
   else:
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    stream=archive.extractfile(member)
    if stream is None:raise ValueError('A worker release entry is unreadable')
    with target.open('xb') as output:shutil.copyfileobj(stream,output)
    target.chmod(0o600)

def google_configuration(base_url: str,root: Path=ROOT,source: Path=SOURCE,models: Path=MODELS,python: Path=PYTHON) -> tuple[dict,dict]:
 from urllib.parse import urlsplit
 parsed=urlsplit(base_url)
 if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:raise ValueError('A private HTTPS gateway base URL is required')
 profile_path=root/'profile.json'
 profile={'private_runtime':True,'entrypoint':str(source/'main.py'),'runtime_directory':str(root/'runtime'),'extra_model_paths':'/opt/recastfx/expanded-extra-model-paths.yaml','required_paths':[str(source/'comfy_extras/nodes_minimax_h3.py')],'whitelist':['recastfx_worker_nodes'],'arguments':['--disable-dynamic-vram','--reserve-vram','20','--cache-none']}
 config={'base_url':base_url.rstrip('/'),'route':'google_h3','worker_id':'google-character-swap-1','spool':str(root/'jobs'),'comfy_url':'http://127.0.0.1:8201','launch_commands':{phase:[str(python),'-m','self_hosted_worker.launcher','--profile',str(profile_path),'--phase',phase,'--port','8201'] for phase in ['encode','render']},'model_paths':{key:str(models/name) for key,name in FILES.items()},'aliases':{key:Path(name).name for key,name in FILES.items()},'minimum_free_disk_gib':20,'minimum_available_ram_gib':12,'startup_available_ram_gib':48,'maximum_job_seconds':14400,'retain_completed_media_hours':24,'guard_comfy_urls':[]}
 return config,profile

def secret_value(resource: str) -> str:
 if not re.fullmatch(r'projects/[A-Za-z0-9_-]+/secrets/[A-Za-z0-9_-]+/versions/[1-9][0-9]*',resource):raise ValueError('A specific Secret Manager version is required')
 data=authenticated_bytes('https://secretmanager.googleapis.com/v1/'+resource+':access',16384)
 token=base64.b64decode(json.loads(data)['payload']['data'],validate=True).decode()
 if not 32<=len(token)<=4096 or any(ord(c)<33 or ord(c)>126 for c in token):raise ValueError('The worker credential is invalid')
 return token

def gpu_preflight() -> None:
 result=subprocess.run(['nvidia-smi','--query-gpu=name,memory.total','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=20)
 rows=[line.split(',') for line in result.stdout.splitlines() if line.strip()]
 if result.returncode or len(rows)!=1 or 'RTX PRO 6000' not in rows[0][0] or float(rows[0][1].strip())<90000:raise ValueError('The full RTX PRO 6000 GPU is not ready')

def link_private_plugin(root: Path,release: Path) -> None:
 custom=root/'runtime/custom_nodes';custom.mkdir(parents=True,exist_ok=True,mode=0o700)
 if not custom.resolve().is_relative_to(root.resolve()):raise ValueError('Private custom nodes escaped the worker root')
 relative=Path('self_hosted_worker/custom_nodes/recastfx_worker_nodes')
 expected=(release/relative).resolve();plugin=custom/'recastfx_worker_nodes'
 if not (expected/'__init__.py').is_file():raise ValueError('The private worker plugin is absent')
 if plugin.is_symlink():
  old=plugin.resolve()
  if old==expected:return
  # On a new boot, only replace our known plugin link from another immutable
  # release. Preserve any unfamiliar directory/link for operator inspection.
  releases=(root/'releases').resolve()
  if not old.is_relative_to(releases) or old.parts[-len(relative.parts):]!=relative.parts:raise ValueError('An unfamiliar private worker plugin is installed')
  plugin.unlink()
 elif plugin.exists():raise ValueError('Preserve the installed private plugin for operator review')
 plugin.symlink_to(expected,target_is_directory=True)

def prepare_release(root: Path=ROOT) -> tuple[Path,Path,str]:
 values={name:metadata('instance/attributes/recastfx-'+name) for name in ['worker-release','worker-release-sha256','worker-config','worker-config-sha256','api-url','worker-token-secret']}
 for name in ['worker-release','worker-config']:
  pin=os.environ.get('RECASTFX_PINNED_'+name.upper().replace('-','_')+'_SHA256','')
  if not HEX.fullmatch(pin) or values[name+'-sha256']!=pin:raise ValueError('Startup artifact pin does not match metadata')
 digest=values['worker-release-sha256'];release=root/'releases'/digest
 root.mkdir(parents=True,exist_ok=True,mode=0o700);root.chmod(0o700)
 data=verified_download(values['worker-release'],digest,16*1024*1024)
 if not release.exists():
  release.parent.mkdir(exist_ok=True,mode=0o700)
  stage=Path(tempfile.mkdtemp(prefix='.release-',dir=release.parent))
  try:extract_release(data,stage);stage.rename(release)
  except BaseException:shutil.rmtree(stage,ignore_errors=True);raise
 config_data=verified_download(values['worker-config'],values['worker-config-sha256'],262144);config=json.loads(config_data)
 expected,profile=google_configuration(values['api-url'],root=root)
 # Gateway-issued arbitrary executables, model aliases, spool paths or route changes
 # must not become machine commands through a mutable configuration artifact.
 if config!=expected:raise ValueError('Pinned worker config differs from the installed Google profile')
 link_private_plugin(root,release)
 for name,obj in [('profile.json',profile),('worker.json',config)]:
  path=root/name;temporary=root/(name+'.partial');temporary.write_text(json.dumps(obj,indent=2));temporary.chmod(0o600);temporary.replace(path)
 return release,root/'worker.json',values['worker-token-secret']

def startup_script(release_sha256: str,config_sha256: str) -> str:
 if not HEX.fullmatch(release_sha256) or not HEX.fullmatch(config_sha256):raise ValueError('Two SHA256 pins are required')
 source=Path(__file__).read_text()
 # Embed the stdlib bootstrap into the immutable startup artifact; no bootstrap
 # installer or runtime dependency fetch is needed on the model disk.
 return '#!/bin/bash\nset -euo pipefail\numask 077\ntrap \'sync; /sbin/shutdown -h now\' EXIT\nexport OMP_NUM_THREADS=8 MKL_NUM_THREADS=8 OPENBLAS_NUM_THREADS=8 TOKENIZERS_PARALLELISM=false\nexport RECASTFX_PINNED_WORKER_RELEASE_SHA256='+shlex.quote(release_sha256)+'\nexport RECASTFX_PINNED_WORKER_CONFIG_SHA256='+shlex.quote(config_sha256)+'\n/usr/bin/python3 - <<\'RECASTFX_BOOTSTRAP_PY\'\n'+source+'\nRECASTFX_BOOTSTRAP_PY\n'

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--render-startup',action='store_true');parser.add_argument('--release-sha256');parser.add_argument('--config-sha256');args=parser.parse_args()
 if args.render_startup:print(startup_script(args.release_sha256 or '',args.config_sha256 or ''));return
 try:
  release,config,secret=prepare_release();gpu_preflight()
  token=secret_value(secret);environment=dict(os.environ,SELF_HOSTED_WORKER_TOKEN=token,PYTHONPATH=str(release));environment.pop('RECASTFX_WORKER_TOKEN',None)
  if not PYTHON.is_file():raise ValueError('The existing Google Python environment is unavailable')
  # The runner performs strict local model/profile validation before heartbeat.
  os.execve(str(PYTHON),[str(PYTHON),'-u','-m','self_hosted_worker.runner','--config',str(config)],environment)
 except Exception:
  print('Google worker bootstrap failed validation; no render was submitted.',file=sys.stderr);raise SystemExit(1) from None
if __name__=='__main__':main()
