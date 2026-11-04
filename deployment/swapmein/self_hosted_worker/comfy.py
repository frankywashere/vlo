"""Dedicated Comfy process ownership and conservative submission recovery."""
from __future__ import annotations
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from .media import atomic_json
from .recipe import WorkerError
from .transport import OPENER

class ComfyClient:
    def __init__(self,url: str):
        p=urllib.parse.urlsplit(url)
        if p.scheme!='http' or p.hostname not in {'127.0.0.1','localhost','::1'} or p.username or p.password or p.query or p.fragment:
            raise WorkerError('worker_failed','Comfy must be a private loopback HTTP endpoint.')
        self.url=url.rstrip('/')
    def get(self,path: str) -> dict:
        try:
            with OPENER.open(self.url+'/'+path,timeout=15) as r:return json.loads(r.read(32*1024*1024))
        except (OSError,urllib.error.URLError,ValueError):raise WorkerError('worker_failed','The dedicated renderer is unavailable.') from None
    def available(self) -> bool:
        try:self.get('queue');return True
        except WorkerError:return False
    def post(self,graph: dict,client_id: str) -> dict:
        req=urllib.request.Request(self.url+'/prompt',data=json.dumps({'prompt':graph,'client_id':client_id},allow_nan=False).encode(),headers={'Content-Type':'application/json'})
        try:
            with OPENER.open(req,timeout=35) as r:return json.loads(r.read(1024*1024))
        except urllib.error.HTTPError as e:
            if e.code==400:raise WorkerError('worker_failed','The installed renderer rejected this recipe or its model dependencies.') from None
            raise WorkerError('submission_unknown','Renderer submission was not confirmed; automatic resubmission is disabled.') from None
        except (OSError,urllib.error.URLError,ValueError):raise WorkerError('submission_unknown','Renderer submission was not confirmed; automatic resubmission is disabled.') from None
    def wait(self,prompt_id: str,check,limit: float,poll_seconds: float=3) -> dict:
        started=time.monotonic()
        while True:
            check()
            history=self.get('history/'+urllib.parse.quote(prompt_id,safe='')).get(prompt_id)
            if history:
                if history.get('status',{}).get('status_str')!='success' or not history.get('status',{}).get('completed'):
                    raise WorkerError('worker_failed','The dedicated renderer failed a generation step.')
                return history
            if time.monotonic()-started>limit:raise WorkerError('worker_failed','A renderer step exceeded its bounded runtime.')
            time.sleep(poll_seconds)

def exact_process(record: dict) -> bool:
    if not record.get('pid') or not isinstance(record.get('command'),list):return False
    path=Path('/proc')/str(record['pid'])/'cmdline'
    try:return path.read_bytes().decode().split(chr(0))[:-1]==record['command']
    except (FileNotFoundError,PermissionError):return False

def stop_owned_record(record: dict) -> None:
    if not exact_process(record):return
    # A fresh process group is created for our launcher. Never signal by port/name.
    try:os.killpg(record['pid'],signal.SIGTERM)
    except ProcessLookupError:return
    deadline=time.monotonic()+15
    while exact_process(record) and time.monotonic()<deadline:time.sleep(.1)
    if exact_process(record):
        try:os.killpg(record['pid'],signal.SIGKILL)
        except ProcessLookupError:pass

class DedicatedRuntime:
    def __init__(self,client: ComfyClient,argv: list[str],phase: str,spool: Path,directory: Path,journal: dict,save,check):
        self.client=client;self.argv=[s.format(job_dir=str(directory),phase=phase,spool=str(spool)) for s in argv]
        self.phase=phase;self.spool=spool;self.directory=directory;self.journal=journal;self.save=save;self.check=check;self.proc=None;self.log=None
    def __enter__(self):
        self.check()
        # Recover only our exact process, never adopt an arbitrary shared endpoint.
        record=self.journal.get('launcher') or {}
        if self.client.available():
            if exact_process(record) and record.get('phase')==self.phase:
                self.record=record;return self
            raise WorkerError('worker_failed','The dedicated renderer port is already occupied; shared services were left untouched.')
        if exact_process(record):stop_owned_record(record)
        self.log=(self.directory/f'{self.phase}-runtime.log').open('ab');os.chmod(self.log.name,0o600)
        env=os.environ.copy();env['RECASTFX_WORKER_SPOOL']=str(self.spool);env['PYTHONDONTWRITEBYTECODE']='1';env['RECASTFX_WORKER_OWNER_PID']=str(os.getpid())
        # The worker credential is outbound-only and not needed by Comfy/plugins.
        for name in ['RECASTFX_WORKER_TOKEN','SELF_HOSTED_WORKER_TOKEN','SELF_HOSTED_GOOGLE_H3_WORKER_TOKEN','SELF_HOSTED_PAPERSPACE_H3_WORKER_TOKEN']:env.pop(name,None)
        try:
            self.proc=subprocess.Popen(self.argv,stdout=self.log,stderr=subprocess.STDOUT,env=env,start_new_session=True)
            self.record={'pid':self.proc.pid,'command':self.argv,'phase':self.phase};self.journal['launcher']=self.record;self.save()
            owner_file=self.directory/f'{self.phase}-owned-process.json';atomic_json(owner_file,self.record)
            deadline=self.journal['hard_deadline_epoch']
            self.watchdog=subprocess.Popen([sys.executable,'-m','self_hosted_worker.watchdog','--record',str(owner_file),'--deadline',str(deadline),'--parent',str(os.getpid())],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,start_new_session=True,env=env)
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                self.check()
                if self.proc.poll() is not None:raise WorkerError('worker_failed','The dedicated runtime exited during startup.')
                if self.client.available():return self
                time.sleep(1)
            raise WorkerError('worker_failed','The dedicated runtime did not become ready in time.')
        except BaseException:self.__exit__(None,None,None);raise
    def __exit__(self,*_args):
        if self.proc is not None and self.proc.poll() is None:
            os.killpg(self.proc.pid,signal.SIGTERM)
            try:self.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:os.killpg(self.proc.pid,signal.SIGKILL);self.proc.wait(timeout=5)
        elif getattr(self,'record',None):stop_owned_record(self.record)
        if self.log:self.log.close()
        watchdog=getattr(self,'watchdog',None)
        if watchdog is not None:
            try:watchdog.wait(timeout=2)
            except subprocess.TimeoutExpired:watchdog.terminate();watchdog.wait(timeout=2)
        self.journal['launcher']={};self.save()

class GraphExecutor:
    def __init__(self,client: ComfyClient,journal: dict,directory: Path,save,check,poll_seconds=3):
        self.client=client;self.journal=journal;self.directory=directory;self.save=save;self.check=check;self.poll_seconds=poll_seconds
    def recover_existing(self,artifact_ok):
        # A failed/lost submit response is a quarantine condition, never a retry.
        for key,task in self.journal.get('tasks',{}).items():
            if task.get('status')=='success':continue
            if artifact_ok(key):task['status']='success';self.save();continue
            if task.get('status')=='posting':raise WorkerError('submission_unknown','An earlier renderer submission has an unknown outcome; it will not be repeated.')
            if task.get('prompt_id'):
                if not self.client.available():raise WorkerError('submission_unknown','The renderer lost an outstanding prompt; it will not be repeated automatically.')
                history=self.client.wait(task['prompt_id'],self.check,task['limit_seconds'],self.poll_seconds)
                if not artifact_ok(key):raise WorkerError('worker_failed','A successful renderer step produced no verified artifact.')
                task.update(status='success',history_status=history['status']);self.save()
        record=self.journal.get('launcher') or {}
        if record:stop_owned_record(record);self.journal['launcher']={};self.save()
    def execute(self,key: str,graph: dict,limit_seconds: int,artifact_ok) -> None:
        tasks=self.journal.setdefault('tasks',{})
        if key in tasks:
            if tasks[key].get('status')=='success' and artifact_ok(key):return
            raise WorkerError('submission_unknown','An existing renderer step cannot be safely resubmitted.')
        info=self.client.get('object_info')
        if not {node['class_type'] for node in graph.values()}<=info.keys():raise WorkerError('worker_failed','The dedicated runtime is missing a required recipe node.')
        queue=self.client.get('queue')
        if queue.get('queue_running') or queue.get('queue_pending'):raise WorkerError('worker_failed','The dedicated renderer is busy; no additional work was submitted.')
        atomic_json(self.directory/f'{key}-graph.json',graph)
        task={'status':'posting','limit_seconds':limit_seconds,'submitted_at':time.time()};tasks[key]=task;self.save()
        answer=self.client.post(graph,self.journal['job_id']+'-'+key)
        if not answer.get('prompt_id') or answer.get('node_errors'):
            raise WorkerError('worker_failed','The installed runtime rejected the submitted recipe.')
        task.update(status='submitted',prompt_id=answer['prompt_id']);self.save()
        history=self.client.wait(task['prompt_id'],self.check,limit_seconds,self.poll_seconds)
        if not artifact_ok(key):raise WorkerError('worker_failed','The completed renderer step has no verified artifact.')
        task.update(status='success',seconds=time.time()-task['submitted_at'],history_status=history['status']);self.save()
