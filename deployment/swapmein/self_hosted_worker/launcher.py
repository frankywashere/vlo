"""Start one private, already-installed Comfy runtime; never install/download models.

A parent worker owns this launcher's process group, including its Comfy child.
Only an explicitly private runtime's loader file may be switched between the
validated original encoding variant and patched single-use rendering variant.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from .runtime_child import protect

def validate_profile(profile_path: Path,route: str | None = None) -> dict:
    """Filesystem/schema checks only: no imports of Comfy, subprocess or model I/O."""
    profile=json.loads(profile_path.read_text())
    if not isinstance(profile,dict) or profile.get('private_runtime') is not True:raise ValueError('Private runtime profile is required')
    runtime=Path(profile['runtime_directory']).expanduser().resolve();entrypoint=Path(profile['entrypoint']).expanduser().resolve()
    if not runtime.is_dir() or not entrypoint.is_file() or not entrypoint.stat().st_size or runtime==entrypoint.parent:raise ValueError('The installed private runtime or entrypoint is missing')
    custom=runtime/'custom_nodes'
    if not custom.is_dir() or not custom.resolve().is_relative_to(runtime):raise ValueError('Custom nodes must remain inside the private runtime')
    source=Path(__file__).resolve().parent/'custom_nodes'/'recastfx_worker_nodes';plugin=custom/'recastfx_worker_nodes'
    if not (source/'__init__.py').is_file():raise ValueError('The installed worker node source is missing')
    if plugin.is_symlink():
        if plugin.resolve()!=source:raise ValueError('A different worker plugin is already present')
    elif plugin.exists():raise ValueError('Preserve the existing private plugin for operator review')
    paths=Path(profile['extra_model_paths']).expanduser().resolve()
    if not paths.is_file() or not paths.stat().st_size:raise ValueError('The installed model path configuration is missing')
    whitelist=profile.get('whitelist',['recastfx_worker_nodes'])
    if not isinstance(whitelist,list) or any(not isinstance(x,str) or not x or '/' in x or '..' in x for x in whitelist) or 'recastfx_worker_nodes' not in whitelist:raise ValueError('The private worker nodes must be whitelisted')
    variants=profile.get('loader_variants')
    if route in {'paperspace','paperspace_h3'} and (not variants or 'ComfyUI-MultiGPU' not in whitelist):raise ValueError('Paperspace requires the tested separate loader variants')
    if variants:
        if not isinstance(variants,dict) or set(variants)!={'encode','render'}:raise ValueError('Encoding and rendering variants are both required')
        files=[Path(variants[k]).expanduser().resolve() for k in ['encode','render']]
        if any(not x.is_file() or not x.stat().st_size for x in files) or files[0].samefile(files[1]):raise ValueError('Distinct installed loader variants are required')
        destination=(runtime/profile['loader_destination']).resolve()
        if not destination.is_relative_to(runtime) or not destination.parent.is_dir():raise ValueError('Loader switching must remain inside the private runtime')
    required=profile.get('required_paths',[])
    if not isinstance(required,list) or any(not isinstance(x,str) or not Path(x).expanduser().exists() for x in required):raise ValueError('A declared runtime dependency is missing')
    arguments=profile.get('arguments',[])
    blocked={'--listen','--port','--base-directory','--input-directory','--output-directory','--temp-directory','--user-directory','--extra-model-paths-config','--disable-all-custom-nodes','--whitelist-custom-nodes'}
    if not isinstance(arguments,list) or any(not isinstance(x,str) or x.split('=',1)[0] in blocked for x in arguments):raise ValueError('Extra arguments cannot override private runtime isolation')
    return profile

def validate_launch_command(argv: list[str],phase: str,comfy_port: int,route: str) -> None:
    if len(argv)<3 or argv[1:3]!=['-m','self_hosted_worker.launcher']:raise ValueError('Use the foreground private runtime launcher')
    options={};parts=argv[3:]
    if len(parts)%2:raise ValueError('A launcher option is incomplete')
    for key,value in zip(parts[::2],parts[1::2]):
        if key not in {'--profile','--phase','--port'} or key in options:raise ValueError('The launcher options are invalid')
        options[key]=value
    if options.get('--phase')!=phase or int(options.get('--port','8201'))!=comfy_port:raise ValueError('Runtime phase or port does not match the worker configuration')
    path=options.get('--profile','')
    if not path or '{' in path or '}' in path:raise ValueError('A static installed profile path is required')
    validate_profile(Path(path).expanduser().resolve(),route)

def launch(profile_path: Path,phase: str,port: int):
    owner=int(os.environ['RECASTFX_WORKER_OWNER_PID']);protect(owner)
    profile=validate_profile(profile_path)
    runtime=Path(profile['runtime_directory']).expanduser().resolve();entrypoint=Path(profile['entrypoint']).expanduser().resolve()
    if not 1024<=port<=65535:raise ValueError('Invalid private runtime port')
    plugin=runtime/'custom_nodes'/'recastfx_worker_nodes';source=Path(__file__).resolve().parent/'custom_nodes'/'recastfx_worker_nodes'
    if plugin.is_symlink():
        if plugin.resolve()!=source:raise ValueError('A different worker plugin is already present')
    elif plugin.exists():raise ValueError('Preserve the existing worker plugin; operator review is required')
    else:plugin.symlink_to(source,target_is_directory=True)
    variants=profile.get('loader_variants')
    if variants:
        variant=Path(variants[phase]).resolve();destination=(runtime/profile['loader_destination']).resolve()
        if not destination.is_relative_to(runtime) or not variant.is_file() or not destination.parent.is_dir():raise ValueError('Loader switching must remain inside the private runtime')
        shutil.copy2(variant,destination)
    paths=Path(profile['extra_model_paths']).resolve()
    if not paths.is_file():raise ValueError('Installed model paths are not configured')
    for name in ['inputs','outputs','temporary','user']:(runtime/name).mkdir(exist_ok=True)
    command=[sys.executable,str(entrypoint),'--listen','127.0.0.1','--port',str(port),'--base-directory',str(runtime),'--input-directory',str(runtime/'inputs'),'--output-directory',str(runtime/'outputs'),'--temp-directory',str(runtime/'temporary'),'--user-directory',str(runtime/'user'),'--extra-model-paths-config',str(paths),'--preview-method','none','--disable-auto-launch','--disable-api-nodes','--disable-all-custom-nodes','--whitelist-custom-nodes',*profile.get('whitelist',['recastfx_worker_nodes']),'--database-url','sqlite:///:memory:',*profile.get('arguments',[])]
    # No shell/exec replacement: outer PID identity stays stable for watchdogs.
    return subprocess.call([sys.executable,'-m','self_hosted_worker.runtime_child','--parent',str(os.getpid()),*command],cwd=entrypoint.parent)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--profile',required=True,type=Path);p.add_argument('--phase',choices=['encode','render'],required=True);p.add_argument('--port',type=int,default=8201);a=p.parse_args()
    try:raise SystemExit(launch(a.profile,a.phase,a.port))
    except (OSError,ValueError,KeyError,TypeError,RuntimeError):print('Private runtime profile is incomplete or unsafe.',file=sys.stderr);raise SystemExit(1) from None
if __name__=='__main__':main()
