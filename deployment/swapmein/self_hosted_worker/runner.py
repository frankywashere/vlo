"""Run with python -m self_hosted_worker.runner --config /private/worker.json.

Only outbound claims, private media transfers and owned local subprocesses.
This module does not provision cloud resources, install services or download models.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from dataclasses import dataclass,field
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import threading
import time
from .comfy import ComfyClient,DedicatedRuntime,GraphExecutor,stop_owned_record
from .launcher import validate_launch_command
from .media import assemble,atomic_json,copy_untouched,digest,inspect_source,verify_part
from .recipe import RECIPE_VERSION,WorkerError,build_graph,validate_plan
from .transport import ControlClient,ControlRejected,SignedURLExpired,download,upload

SAFE=re.compile(r'^[A-Za-z0-9_-]{1,96}$')
MODEL_KEYS={'model','text','vae','audio_vae','turbo','character'}

@dataclass
class Config:
    base_url: str
    route: str
    worker_id: str
    spool: Path
    launch_commands: dict
    model_paths: dict
    aliases: dict=field(default_factory=dict)
    comfy_url: str='http://127.0.0.1:8201'
    allow_local_test_urls: bool=False
    heartbeat_seconds: float=15
    lease_grace_seconds: float=90
    poll_seconds: float=3
    idle_seconds: float=15
    maximum_canvas_pixels: int=1536*864
    minimum_free_disk_gib: float=20
    minimum_available_ram_gib: float=12
    startup_available_ram_gib: float=48
    maximum_job_seconds: int=14400
    retain_completed_media_hours: float=24
    guard_comfy_urls: list[str]=field(default_factory=list)
    token: str=field(default='',repr=False)
    @classmethod
    def from_file(cls,path: Path):
        try:data=json.loads(path.read_text());data['spool']=Path(data['spool']).expanduser().resolve();data['token']=os.environ.get('SELF_HOSTED_WORKER_TOKEN') or os.environ.get('RECASTFX_WORKER_TOKEN','');return cls(**data)
        except (OSError,ValueError,TypeError,KeyError):raise WorkerError('worker_failed','The worker configuration is invalid.') from None
    def validate_profiles(self):
        from urllib.parse import urlsplit
        try:
            parsed=urlsplit(self.comfy_url)
            if parsed.hostname not in {'127.0.0.1','localhost'}:raise ValueError('The owned launcher binds IPv4 loopback')
            port=parsed.port or 80
            for phase,command in self.launch_commands.items():validate_launch_command(command,phase,port,self.route)
        except (OSError,ValueError,KeyError,TypeError,OverflowError):
            raise WorkerError('worker_failed','The installed private runtime profile or its dependencies are not ready.') from None
    def validate(self):
        self.route={'google':'google_h3','paperspace':'paperspace_h3'}.get(self.route,self.route)
        if self.route not in {'google_h3','paperspace_h3'} or not SAFE.fullmatch(self.worker_id) or len(self.worker_id)>80:raise WorkerError('worker_failed','Worker identity or route is invalid.')
        if not self.token:raise WorkerError('worker_failed','SELF_HOSTED_WORKER_TOKEN is required.')
        if set(self.model_paths)!=MODEL_KEYS:raise WorkerError('worker_failed','All six installed model paths must be configured.')
        if set(self.launch_commands)!={'encode','render'}:raise WorkerError('worker_failed','Dedicated encode and render launch commands are required.')
        for cmd in self.launch_commands.values():
            if not isinstance(cmd,list) or not cmd or any(not isinstance(s,str) for s in cmd) or not (Path(cmd[0]).is_file() or shutil.which(cmd[0])):
                raise WorkerError('worker_failed','A dedicated runtime launch command is invalid.')
        self.validate_profiles()
        if not isinstance(self.retain_completed_media_hours,(int,float)) or not 0<=self.retain_completed_media_hours<=720:raise WorkerError('worker_failed','Local media retention is invalid.')
        if self.maximum_job_seconds<1 or self.maximum_job_seconds>14400:raise WorkerError('worker_failed','The worker hard cap must not exceed four hours.')
        for path in self.model_paths.values():
            if not Path(path).is_file() or Path(path).stat().st_size==0:raise WorkerError('worker_failed','A required local model file is missing; no model was downloaded.')
        self.spool.mkdir(parents=True,exist_ok=True);os.chmod(self.spool,0o700)
        # Private media/journals must never be staged inside a Git checkout.
        if any((p/'.git').exists() for p in [self.spool,*self.spool.parents]):raise WorkerError('worker_failed','The private worker spool must be outside the repository.')
        ComfyClient(self.comfy_url)
        ControlClient(self.base_url,self.route,self.token,self.worker_id,self.allow_local_test_urls)

class LeaseGuard:
    def __init__(self,control: ControlClient,lease: dict,config: Config,job_started_at: float):
        self.control=control;self.lease=lease;self.config=config;self.stopped=threading.Event();self.lost=threading.Event();self.started=time.monotonic()-max(0,time.time()-job_started_at);self.last_success=time.monotonic();self.deadline=None
    def __enter__(self):
        self.renew();self.thread=threading.Thread(target=self.loop,name='private-worker-heartbeat',daemon=True);self.thread.start();return self
    def renew(self):
        answer=self.control.call('heartbeat',{**self.lease,'recipe_version':RECIPE_VERSION})
        if answer.get('ok') is False:raise WorkerError('worker_failed','The server no longer owns this worker lease.')
        self.last_success=time.monotonic();value=answer.get('lease_expires_at')
        if value is not None:
            try:
                if isinstance(value,str) and not value.replace('.','',1).isdigit():epoch=datetime.datetime.fromisoformat(value.replace('Z','+00:00')).timestamp()
                else:epoch=float(value)
                self.deadline=time.monotonic()+max(0,epoch-time.time())
            except (TypeError,ValueError,OverflowError):raise WorkerError('worker_failed','The worker lease expiry is invalid.') from None
    def loop(self):
        while not self.stopped.wait(self.config.heartbeat_seconds):
            try:self.renew()
            except ControlRejected:
                self.lost.set();return
            except WorkerError:
                if time.monotonic()-self.last_success>self.config.lease_grace_seconds or self.deadline is not None and time.monotonic()>=self.deadline:
                    self.lost.set();return
    def check(self,startup=False):
        if self.lost.is_set() or self.deadline is not None and time.monotonic()>=self.deadline:
            raise WorkerError('worker_failed','Worker lease renewal stopped; only this job runtime will be stopped.')
        if time.monotonic()-self.started>=self.config.maximum_job_seconds:raise WorkerError('worker_failed','The four-hour worker hard cap was reached.')
        if shutil.disk_usage(self.config.spool).free<self.config.minimum_free_disk_gib*2**30:raise WorkerError('worker_failed','Private worker disk headroom is below the configured safety limit.')
        mem=Path('/proc/meminfo')
        if mem.exists():
            available=int(next(x.split()[1] for x in mem.read_text().splitlines() if x.startswith('MemAvailable:')))*1024
            minimum=self.config.startup_available_ram_gib if startup else self.config.minimum_available_ram_gib
            if available<minimum*2**30:raise WorkerError('worker_failed','Available worker memory is below the configured safety limit.')
    def __exit__(self,*_args):
        self.stopped.set();self.thread.join(timeout=26)

class Worker:
    def __init__(self,config: Config,control=None,comfy=None):
        self.config=config;self.control=control or ControlClient(config.base_url,config.route,config.token,config.worker_id,config.allow_local_test_urls);self.comfy=comfy or ComfyClient(config.comfy_url)
    def capacity_available(self):
        try:self.config.validate_profiles()
        except WorkerError:return False
        if shutil.disk_usage(self.config.spool).free<self.config.minimum_free_disk_gib*2**30:return False
        if any(not Path(p).is_file() for p in self.config.model_paths.values()):return False
        mem=Path('/proc/meminfo')
        if mem.exists():
            available=int(next(x.split()[1] for x in mem.read_text().splitlines() if x.startswith('MemAvailable:')))*1024
            if available<self.config.startup_available_ram_gib*2**30:return False
        try:
            for endpoint in self.config.guard_comfy_urls:
                q=ComfyClient(endpoint).get('queue')
                if q.get('queue_running') or q.get('queue_pending'):return False
        except WorkerError:return False
        return True
    def idle_ready(self):
        # Revalidate even for direct callers: no unconfigured heartbeat can mark ready.
        self.config.validate()
        return self.control.call('heartbeat',{'recipe_version':RECIPE_VERSION})
    def refresh(self,lease: dict):return self.control.call('refresh',lease)
    def stage(self,job: dict,directory: Path,journal: dict,save,guard):
        references=job.get('references')
        if not isinstance(references,list) or not references or len(references)>5:raise WorkerError('mapping_required','Between one and five replacement references must be staged.')
        indices=[r.get('index') for r in references]
        if any(type(i) is not int or not 0<=i<=99 for i in indices) or len(set(indices))!=len(indices):raise WorkerError('mapping_required','Reference identifiers are invalid or duplicated.')
        transfers=[('source',job.get('source_url'),directory/'source.mp4',512*1024*1024)]
        transfers += [(f'reference-{r["index"]}',r.get('url'),directory/f'reference-{r["index"]}.image',32*1024*1024) for r in references]
        for key,url,path,maximum in transfers:
            saved=journal.setdefault('inputs',{}).get(key)
            if saved and path.is_file() and digest(path)==saved['sha256']:continue
            if saved:raise WorkerError('worker_failed','A previously staged private input changed.')
            try:download(url or '',path,maximum,self.config.allow_local_test_urls,guard.check)
            except SignedURLExpired:
                updated=self.refresh({'job_id':job['id'],'lease_token':journal['lease_token']})
                url=updated.get('source_url') if key=='source' else next((r.get('url') for r in updated.get('references',[]) if f'reference-{r.get("index")}'==key),None)
                download(url or '',path,maximum,self.config.allow_local_test_urls,guard.check)
            journal['inputs'][key]={'sha256':digest(path),'bytes':path.stat().st_size};save()
        return set(indices)
    def process(self,job: dict):
        key=job.get('id')
        if not isinstance(key,str) or not SAFE.fullmatch(key) or not isinstance(job.get('lease_token'),str) or not job['lease_token']:
            raise WorkerError('worker_failed','The claimed job identity is invalid.')
        directory=self.config.spool/key;directory.mkdir(mode=0o700,exist_ok=True);path=directory/'journal.json'
        public_spec={'id':key,'recipe':job.get('recipe') or {},'shots':job.get('shots'),'references':[{'index':r.get('index')} for r in job.get('references',[])]}
        spec_hash=hashlib.sha256(json.dumps(public_spec,sort_keys=True,allow_nan=False).encode()).hexdigest()
        journal=json.loads(path.read_text()) if path.exists() else {'job_id':key,'spec_sha256':spec_hash,'tasks':{},'completed_shots':[],'owner_worker_id':self.config.worker_id,'started_at':float(job.get('worker_started_at') or time.time())}
        if journal['spec_sha256']!=spec_hash:raise WorkerError('mapping_required','The resumed job mapping or recipe changed.')
        journal['lease_token']=job['lease_token'];journal['hard_deadline_epoch']=journal['started_at']+self.config.maximum_job_seconds
        save=lambda:atomic_json(path,journal)
        save();atomic_json(self.config.spool/'active.json',{'job_id':key,'lease_token':job['lease_token'],'spec':public_spec})
        lease={'job_id':key,'lease_token':job['lease_token']}
        try:
            with LeaseGuard(self.control,lease,self.config,journal['started_at']) as guard:
                references=self.stage(job,directory,journal,save,guard)
                shape=inspect_source(directory/'source.mp4',self.config.maximum_canvas_pixels,guard.check)
                shots=validate_plan(job,shape['frames'],references)
                atomic_json(directory/'job.json',{'geometry':shape,'shots':shots,'reference_indexes':sorted(references),'recipe_version':RECIPE_VERSION})
                def artifact_ok(task_key):
                    kind,number=task_key.split('-');index=int(number)
                    if kind=='encode':
                        manifest=directory/'bundles'/f'shot-{index:03d}'/'manifest.json';data=manifest.parent/'tensors.safetensors'
                        try:return data.is_file() and digest(data)==json.loads(manifest.read_text())['sha256']
                        except (OSError,ValueError,KeyError):return False
                    try:verify_part(directory/f'shot-{index:03d}.mp4',shots[index-1]['delivery_frames'],shape,guard.check);return True
                    except WorkerError:return False
                def progress(phase,current=None):
                    self.control.call('progress',{**lease,'phase':phase,'completed_shots':journal['completed_shots'],**({'current_shot':current} if current is not None else {})})
                def shared_guard():
                    guard.check()
                    for endpoint in self.config.guard_comfy_urls:
                        q=ComfyClient(endpoint).get('queue')
                        if q.get('queue_running') or q.get('queue_pending'):raise WorkerError('worker_failed','A shared GPU service became busy; this job runtime was stopped.')
                executor=GraphExecutor(self.comfy,journal,directory,save,shared_guard,self.config.poll_seconds)
                executor.recover_existing(artifact_ok)
                def runtime(phase):
                    self.config.validate_profiles()
                    shared_guard();guard.check(startup=True)
                    return DedicatedRuntime(self.comfy,self.config.launch_commands['encode' if phase=='encode' else 'render'],phase,self.config.spool,directory,journal,save,shared_guard)
                pending=[s for s in shots if s['reference_indexes'] and not artifact_ok(f"encode-{s['index']:03d}")]
                if pending:
                    progress('encoding')
                    with runtime('encode'):
                        for s in pending:
                            progress('encoding',s['index']);executor.execute(f"encode-{s['index']:03d}",build_graph('encode',self.config.route,key,s,shape,self.config.aliases),600,artifact_ok)
                for s in shots:
                    index=s['index'];part=directory/f'shot-{index:03d}.mp4'
                    if index in journal['completed_shots']:
                        verify_part(part,s['delivery_frames'],shape,guard.check);continue
                    if not s['reference_indexes']:copy_untouched(directory/'source.mp4',part,s,shape,guard.check)
                    elif not artifact_ok(f'render-{index:03d}'):
                        progress('rendering',index)
                        with runtime(f'render-{index:03d}'):
                            executor.execute(f'render-{index:03d}',build_graph('render',self.config.route,key,s,shape,self.config.aliases),4800 if s['conditioning_frames']>90 else 3600 if s['conditioning_frames']>56 else 1800,artifact_ok)
                    evidence=verify_part(part,s['delivery_frames'],shape,guard.check);journal.setdefault('outputs',{})[str(index)]=evidence;journal['completed_shots'].append(index);save();progress('rendering',index)
                progress('assembling');out=assemble(directory,shots,shape,guard.check)
                journal['output']={'bytes':out.stat().st_size,'sha256':digest(out)};save()
                if not journal.get('complete_acknowledged'):
                    journal['stage']='uploading';save()
                    for attempt in range(2):
                        upload_reply=self.control.call('upload',{**lease,'output_bytes':out.stat().st_size})
                        if upload_reply.get('already_uploaded') is True:break
                        descriptor=upload_reply.get('output_upload')
                        if not isinstance(descriptor,dict):raise WorkerError('worker_failed','The private output destination is unavailable.')
                        try:upload(descriptor,out,self.config.allow_local_test_urls,guard.check);break
                        except SignedURLExpired:
                            if attempt:raise WorkerError('worker_failed','The refreshed private output authorization was rejected.') from None
                    journal['stage']='completing';save()
                    self.control.call('complete',lease);journal['complete_acknowledged']=True;journal['completed_at']=time.time();journal['stage']='complete';journal['lease_token']=None;save()
                (self.config.spool/'active.json').unlink(missing_ok=True)
                return out
        except Exception as error:
            e=error if isinstance(error,WorkerError) else WorkerError('worker_failed','The local job stopped unexpectedly; no automatic generation retry.')
            journal['failure_code']=e.code;save()
            # Server fail handlers must quarantine submission_unknown, never reroute it.
            if journal.get('stage') not in {'uploading','completing'}:
                try:
                    answer=self.control.call('fail',{**lease,'code':e.code,'message':str(e)})
                    if answer.get('ok') and e.code!='submission_unknown':(self.config.spool/'active.json').unlink(missing_ok=True)
                except WorkerError:pass
            raise e from None
        finally:
            stop_owned_record(journal.get('launcher') or {})
    def resume(self):
        active=self.config.spool/'active.json'
        if not active.exists():return False
        d=json.loads(active.read_text());lease={'job_id':d['job_id'],'lease_token':d['lease_token']};media=self.refresh(lease)
        if media.get('status') in {'finalizing','finishing','completed'}:
            self.control.call('complete',lease)
            path=self.config.spool/d['job_id']/'journal.json';journal=json.loads(path.read_text());journal.update(complete_acknowledged=True,lease_token=None,stage='complete',completed_at=journal.get('completed_at') or time.time());atomic_json(path,journal);active.unlink();return True
        job={**d['spec'],**media,'lease_token':d['lease_token']};self.process(job);return True
    def prune_completed_media(self):
        # Only this worker's acknowledged temporary media is eligible, never models
        # or experiment originals. Hashes/graphs/journals remain for reconciliation.
        active=self.config.spool/'active.json'
        active_id=json.loads(active.read_text()).get('job_id') if active.exists() else None
        for directory in self.config.spool.iterdir():
            if directory.is_symlink() or not directory.is_dir() or not SAFE.fullmatch(directory.name) or directory.name==active_id:continue
            path=directory/'journal.json'
            if not path.is_file():continue
            try:journal=json.loads(path.read_text())
            except (OSError,ValueError):continue
            completed=journal.get('completed_at')
            if journal.get('job_id')!=directory.name or journal.get('owner_worker_id')!=self.config.worker_id or not journal.get('complete_acknowledged') or not isinstance(completed,(int,float)) or time.time()-completed<self.config.retain_completed_media_hours*3600:continue
            for child in directory.iterdir():
                if child.is_symlink():continue
                if child.is_file() and (child.name in {'source.mp4','assembled.mp4','assembled.partial.mp4'} or re.fullmatch(r'reference-\d+\.image(?:\.partial)?|shot-\d{3}(?:\.partial)?\.mp4',child.name)):child.unlink()
                elif child.name=='bundles' and child.is_dir():shutil.rmtree(child)
            journal['media_pruned_at']=time.time();atomic_json(path,journal)
    def run(self,once=False):
        self.config.validate()
        with (self.config.spool/'.worker.lock').open('a') as lock:
            try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise WorkerError('worker_failed','Another worker already owns this private spool.') from None
            if self.resume():
                if once:return
            while True:
                self.prune_completed_media()
                if not self.capacity_available():
                    if once:return
                    time.sleep(self.config.idle_seconds);continue
                self.idle_ready();job=self.control.call('claim',{}).get('job')
                if job is not None:self.process(job)
                if once:return
                time.sleep(self.config.idle_seconds)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--config',required=True,type=Path);parser.add_argument('--once',action='store_true');parser.add_argument('--preflight',action='store_true');args=parser.parse_args()
    signal.signal(signal.SIGTERM,lambda *_:(_ for _ in ()).throw(WorkerError('worker_failed','The worker received a shutdown request.')))
    try:
        config=Config.from_file(args.config);config.validate()
        if args.preflight:print('Local worker configuration and installed model paths verified. No GPU work submitted.');return
        Worker(config).run(args.once)
    except (KeyboardInterrupt,SystemExit):raise
    except WorkerError as e:print(json.dumps({'code':e.code,'message':str(e)}));raise SystemExit(1) from None
    except Exception:print(json.dumps({'code':'worker_failed','message':'The local worker stopped unexpectedly; no automatic render resubmission.'}));raise SystemExit(1) from None
if __name__=='__main__':main()
