"""Worker protocol/recipe recovery proofs. Fake Comfy does CPU video copies only."""
from __future__ import annotations
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading
import time
import urllib.parse
import pytest
from self_hosted_worker import runner
from self_hosted_worker.comfy import ComfyClient,DedicatedRuntime,GraphExecutor
from self_hosted_worker.media import assemble,atomic_json,copy_untouched,digest,frame_hashes,inspect_source,verify_part
from self_hosted_worker.recipe import WorkerError,build_graph,conditioning_frames,geometry,validate_plan
from self_hosted_worker.transport import ControlClient,ControlRejected,download,upload

@pytest.mark.parametrize('frames,expected',[(1,56),(22,56),(56,56),(57,73),(74,90),(95,107),(360,362)])
def test_conditioning_padding_matches_validated_recipe(frames,expected):
    assert conditioning_frames(frames)==expected

def test_multi_reference_graph_preserves_all_slots_including_shared_photo():
    shot={'index':1,'start_frame':0,'end_frame':24,'conditioning_frames':56,'delivery_frames':6,'prompt':'Replace selected person at left with Picture1 and person at right with Picture2.','reference_indexes':[8,3,8],'person_ids':['left','right','background']}
    checked=validate_plan({'shots':[shot]},24,{3,8})[0]
    shape=geometry(1364,768)
    graph=build_graph('encode','paperspace_h3','job',checked,shape)
    assert graph['condition']['inputs']['ref_images.ref_image_1']==['reference_1',0]
    assert graph['condition']['inputs']['ref_images.ref_image_2']==['reference_2',0]
    assert graph['condition']['inputs']['ref_images.ref_image_3']==['reference_3',0]
    assert [graph[f'reference_{i}']['inputs']['reference_index'] for i in [1,2,3]]==[8,3,8]
    assert not any('SAM' in n['class_type'] or 'Mesh' in n['class_type'] for n in graph.values())
    assert shape['pad_left']==6 and shape['pad_top']==0
    portrait=geometry(768,1364);assert portrait['pad_top']==6 and portrait['pad_left']==0

@pytest.mark.parametrize('route,loader,text', [('google_h3','UNETLoader','qwen3vl_32b_minimax_h3_bf16.safetensors'),('paperspace_h3','UNETLoaderDisTorch2MultiGPU','qwen3vl_32b_minimax_h3_int8_convrot.safetensors')])
def test_actual_model_profiles_keep_same_recipe(route,loader,text):
    s={'index':1,'start_frame':0,'end_frame':6,'conditioning_frames':56,'delivery_frames':6,'prompt':'Replace target with Picture1.','reference_indexes':[0]};shape=geometry(64,64)
    e=build_graph('encode',route,'job',s,shape);g=build_graph('render',route,'job',s,shape)
    assert e['text']['inputs']['clip_name']==text
    assert g['model']['class_type']==loader
    assert g['noise']['inputs']['noise_seed']==904239
    assert g['schedule']['inputs']['steps']==8 and g['guider']['inputs']['cfg']==1
    assert g['shift']['inputs']['shift_video']==12 and g['shift']['inputs']['shift_audio']==3
    assert g['turbo']['inputs']['strength_model']==g['character']['inputs']['strength_model']==1

@pytest.mark.parametrize('change', ['gap','missing_reference','duplicate_person','bad_padding','wrong_shift'])
def test_invalid_mapping_and_recipe_do_not_silently_drop_targets(change):
    s={'index':1,'start_frame':0,'end_frame':24,'conditioning_frames':56,'delivery_frames':6,'prompt':'Replace target.','reference_indexes':[0,1],'person_ids':['a','b']};j={'shots':[s],'recipe':{}}
    if change=='gap':s['start_frame']=1
    elif change=='missing_reference':s['reference_indexes']=[0,2]
    elif change=='duplicate_person':s['person_ids']=['a','a']
    elif change=='bad_padding':s['conditioning_frames']=24
    else:j['recipe']={'video_shift':99,'shift_video':12}
    with pytest.raises(WorkerError):validate_plan(j,24,{0,1})

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*_args):pass
    def response(self,data,status=200):
        raw=json.dumps(data).encode() if isinstance(data,dict) else data;self.send_response(status);self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
    def do_GET(self):
        route=urllib.parse.urlsplit(self.path).path;self.server.requests.append(('GET',route,self.headers.get('Authorization')))
        if route=='/queue':return self.response({'queue_running':[],'queue_pending':[]})
        if route=='/object_info':return self.response({n:{} for n in self.server.node_types})
        if route.startswith('/history/'):
            pid=route.rsplit('/',1)[-1];return self.response({pid:self.server.histories[pid]} if pid in self.server.histories else {})
        if route in self.server.files:return self.response(self.server.files[route])
        if route=='/redirect':self.send_response(302);self.send_header('Location',self.server.base+'/must-not-follow');self.end_headers();return
        return self.response(b'no',404)
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])));self.server.requests.append(('POST',self.path,self.headers.get('Authorization')))
        if self.path!='/prompt':return self.response({'ok':True})
        self.server.submissions.append(body)
        if self.server.unknown_submit:
            self.close_connection=True;return
        g=body['prompt'];pid='prompt-'+str(len(self.server.submissions));directory=self.server.directory
        if 'persist' in g:
            i=g['persist']['inputs']['shot_index'];p=directory/'bundles'/f'shot-{i:03d}';p.mkdir(parents=True);data=b'CPU fake conditioning';(p/'tensors.safetensors').write_bytes(data);atomic_json(p/'manifest.json',{'sha256':hashlib.sha256(data).hexdigest()})
        else:
            i=g['save']['inputs']['shot_index'];m=json.loads((directory/'job.json').read_text());s=m['shots'][i-1];copy_untouched(directory/'source.mp4',directory/f'shot-{i:03d}.mp4',s,m['geometry'])
        self.server.histories[pid]={'status':{'completed':True,'status_str':'success'}};return self.response({'prompt_id':pid})
    def do_PUT(self):
        self.server.uploaded=self.rfile.read(int(self.headers['Content-Length']));self.response(b'')

@contextmanager
def fake_service(directory):
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;server.directory=directory;server.files={};server.submissions=[];server.histories={};server.unknown_submit=False;server.requests=[];server.uploaded=None;server.base=f'http://127.0.0.1:{server.server_port}'
    s={'index':1,'start_frame':0,'end_frame':6,'conditioning_frames':56,'delivery_frames':6,'prompt':'Target','reference_indexes':[0]};shape=geometry(64,64);server.node_types={x['class_type'] for k in ['encode','render'] for x in build_graph(k,'paperspace','job',s,shape).values()}
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server
    finally:server.shutdown();server.server_close();thread.join(timeout=5)

@pytest.fixture
def tiny_source(tmp_path):
    path=tmp_path/'tiny.mp4'
    subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','testsrc2=size=64x64:rate=24','-f','lavfi','-i','sine=frequency=440:sample_rate=32000','-frames:v','18','-t','0.75','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac',str(path)],check=True)
    return path

def test_authenticated_control_never_leaks_bearer_to_media_or_redirect(tmp_path):
    with fake_service(tmp_path) as server:
        server.files['/input']=b'private source'
        control=ControlClient(server.base,'google','worker-secret','worker',True);assert control.call('claim',{})['ok']
        download(server.base+'/input?sig=private-signature',tmp_path/'download',100,True)
        assert server.requests[0]==('POST','/worker/self-hosted/google_h3/claim','Bearer worker-secret')
        assert server.requests[1]==('GET','/input',None)
        with pytest.raises(WorkerError):download(server.base+'/redirect',tmp_path/'other',100,True)
        assert not any(p=='/must-not-follow' for _,p,_ in server.requests)
        with pytest.raises(WorkerError):upload({'url':server.base+'/output','headers':{'Authorization':'not-forwarded'}},tmp_path/'download',True)

def test_unknown_comfy_submit_is_journaled_once_and_never_retried(tmp_path):
    with fake_service(tmp_path) as server:
        server.unknown_submit=True;journal={'job_id':'job','tasks':{}};save=lambda:atomic_json(tmp_path/'journal.json',journal);client=ComfyClient(server.base);executor=GraphExecutor(client,journal,tmp_path,save,lambda:None,poll_seconds=.001)
        graph={'node':{'class_type':next(iter(server.node_types)),'inputs':{}}}
        with pytest.raises(WorkerError,match='not confirmed'):executor.execute('render-001',graph,10,lambda _:False)
        assert journal['tasks']['render-001']['status']=='posting'
        assert len(server.submissions)==1
        with pytest.raises(WorkerError,match='unknown outcome'):executor.recover_existing(lambda _:False)
        assert len(server.submissions)==1

def test_owned_launcher_never_adopts_or_kills_an_unrelated_service(tmp_path):
    with fake_service(tmp_path) as server:
        journal={};ctx=DedicatedRuntime(ComfyClient(server.base),['unused-command'],'render',tmp_path,tmp_path,journal,lambda:None,lambda:None)
        with pytest.raises(WorkerError,match='already occupied'):ctx.__enter__()
        assert ComfyClient(server.base).available()

class NoopRuntime:
    def __init__(self,*_args,**_kwargs):pass
    def __enter__(self):return self
    def __exit__(self,*_args):pass

class FakeControl:
    def __init__(self,server,already_uploaded=False):self.server=server;self.calls=[];self.already_uploaded=already_uploaded
    def call(self,action,body):
        self.calls.append((action,body))
        if action=='heartbeat':return {'ok':True,'lease_expires_at':time.time()+120}
        if action=='upload':
            assert body['output_bytes']>0
            if self.already_uploaded:return {'already_uploaded':True,'output_upload':None}
            return {'output_upload':{'url':self.server.base+'/output?sig=private-upload-signature','headers':{'Content-Type':'video/mp4'}}}
        return {'ok':True}

@pytest.mark.parametrize('already_uploaded',[False,True])
def test_worker_tiny_video_staging_two_targets_passthrough_assembly_and_replay(tmp_path,tiny_source,monkeypatch,already_uploaded,local_worker_config):
    spool=tmp_path/'private';spool.mkdir();directory=spool/'job';directory.mkdir()
    with fake_service(directory) as server:
        server.files['/source']=tiny_source.read_bytes();server.files['/ref0']=b'fake reference0';server.files['/ref1']=b'fake reference1'
        c=local_worker_config[0];c.base_url=server.base;c.spool=spool;c.comfy_url=server.base;c.allow_local_test_urls=True;c.minimum_free_disk_gib=0;c.minimum_available_ram_gib=0;c.startup_available_ram_gib=0;c.poll_seconds=.001
        for command in c.launch_commands.values():command.extend(['--port',str(server.server_port)])
        control=FakeControl(server,already_uploaded);worker=runner.Worker(c,control=control);monkeypatch.setattr(runner,'DedicatedRuntime',NoopRuntime)
        shots=[{'index':1,'start_frame':0,'end_frame':6,'conditioning_frames':56,'reference_indexes':[0],'person_ids':['caller'],'prompt':'Replace caller with Picture1.'},{'index':2,'start_frame':6,'end_frame':12,'conditioning_frames':56,'reference_indexes':[0,1],'person_ids':['left','right'],'prompt':'Replace left with Picture1 and right with Picture2.'},{'index':3,'start_frame':12,'end_frame':18,'conditioning_frames':56,'reference_indexes':[],'person_ids':[],'prompt':''}]
        job={'id':'job','lease_token':'private-lease','worker_started_at':time.time(),'source_url':server.base+'/source?sig=private-source-signature','references':[{'index':0,'url':server.base+'/ref0?sig=private-reference-signature'},{'index':1,'url':server.base+'/ref1?sig=private-reference-signature'}],'shots':shots,'recipe':{'steps':8,'seed':904239,'cfg':1,'shift_video':12,'shift_audio':3}}
        out=worker.process(job);assert len(server.submissions)==4
        assert (server.uploaded is None if already_uploaded else server.uploaded==out.read_bytes());assert inspect_source(out)['frames']==18
        journal=json.loads((directory/'journal.json').read_text());assert journal['completed_shots']==[1,2,3] and journal['complete_acknowledged'] and journal['lease_token'] is None
        assert 'signature' not in (directory/'journal.json').read_text()
        assert 'private-source-signature' not in (directory/'active.json').read_text() if (directory/'active.json').exists() else True
        again=worker.process(job);assert again==out and len(server.submissions)==4
        assert all(k!='fail' for k,_ in control.calls)
        # Every output frame is one ordered raw-part frame; no generation occurred here.
        assert frame_hashes(out)==sum([frame_hashes(directory/f'shot-{i:03d}.mp4') for i in [1,2,3]],[])

def test_authoritative_first_claim_cap_is_not_reset_on_resume(tmp_path):
    c=runner.Config('https://example.com','google_h3','worker',tmp_path,{}, {},minimum_free_disk_gib=0,minimum_available_ram_gib=0,startup_available_ram_gib=0,maximum_job_seconds=1)
    class Control:
        def call(self,*_args):return {'ok':True,'lease_expires_at':time.time()+180}
    with runner.LeaseGuard(Control(),{'job_id':'job','lease_token':'lease'},c,time.time()-2) as guard:
        with pytest.raises(WorkerError,match='hard cap'):guard.check()

def test_permanent_lease_rejection_stops_work_immediately(tmp_path):
    c=runner.Config('https://example.com','google_h3','worker',tmp_path,{}, {},heartbeat_seconds=.01,minimum_free_disk_gib=0,minimum_available_ram_gib=0,startup_available_ram_gib=0)
    class Control:
        count=0
        def call(self,*_args):
            self.count+=1
            if self.count>1:raise ControlRejected('worker_failed','Ownership rejected.')
            return {'ok':True,'lease_expires_at':time.time()+180}
    with runner.LeaseGuard(Control(),{},c,time.time()) as guard:
        assert guard.lost.wait(.5)
        with pytest.raises(WorkerError,match='lease renewal'):guard.check()

def test_lost_complete_ack_is_reconciled_without_render_or_upload(tmp_path,monkeypatch):
    directory=tmp_path/'job';directory.mkdir();atomic_json(tmp_path/'active.json',{'job_id':'job','lease_token':'private-lease','spec':{'id':'job'}});atomic_json(directory/'journal.json',{'lease_token':'private-lease','complete_acknowledged':False,'stage':'completing'})
    class Control:
        calls=[]
        def call(self,action,body):
            self.calls.append(action)
            if action=='refresh':return {'status':'finalizing','source_url':'https://storage.googleapis.com/unused','references':[]}
            assert action=='complete';return {'ok':True,'status':'finalizing'}
    c=runner.Config('https://example.com','google_h3','worker',tmp_path,{}, {});client=Control();worker=runner.Worker(c,control=client)
    monkeypatch.setattr(worker,'process',lambda *_:pytest.fail('A completed remote job must not regenerate'))
    assert worker.resume();assert client.calls==['refresh','complete'];assert not (tmp_path/'active.json').exists();assert json.loads((directory/'journal.json').read_text())['lease_token'] is None

def test_watchdog_signals_only_an_exact_owned_process(tmp_path,monkeypatch):
    from self_hosted_worker import watchdog
    record=tmp_path/'owned.json';atomic_json(record,{'pid':12345,'command':['private-renderer']});kills=[]
    monkeypatch.setattr(watchdog.os,'killpg',lambda pid,sig:kills.append((pid,sig)));monkeypatch.setattr(watchdog.time,'time',lambda:20)
    monkeypatch.setattr(watchdog,'matches',lambda _:False);watchdog.watch(record,10,1);assert kills==[]
    states=iter([True,False,False]);monkeypatch.setattr(watchdog,'matches',lambda _:next(states,False));watchdog.watch(record,10,1);assert kills==[(12345,watchdog.signal.SIGTERM)]

def test_retention_prunes_only_acknowledged_owned_temp_media(tmp_path):
    c=runner.Config('https://example.com','google_h3','worker',tmp_path,{}, {},retain_completed_media_hours=1)
    class Control:pass
    w=runner.Worker(c,control=Control())
    for name,ack,owner in [('done',True,'worker'),('unknown',False,'worker'),('other',True,'other-worker')]:
        p=tmp_path/name;p.mkdir();(p/'source.mp4').write_bytes(b'private temp');(p/'models.safetensors').write_bytes(b'never delete');atomic_json(p/'journal.json',{'job_id':name,'owner_worker_id':owner,'complete_acknowledged':ack,'completed_at':time.time()-7200})
    w.prune_completed_media()
    assert not (tmp_path/'done/source.mp4').exists()
    assert (tmp_path/'done/models.safetensors').exists() and (tmp_path/'done/journal.json').exists()
    assert (tmp_path/'unknown/source.mp4').exists() and (tmp_path/'other/source.mp4').exists()

def test_worker_rejects_target_slot_mismatch_instead_of_dropping_person():
    shot={'index':1,'start_frame':0,'end_frame':24,'conditioning_frames':56,'prompt':'Replace targets.','reference_indexes':[0],'person_ids':['left'],'targets':[{'id':'left','reference_index':0},{'id':'right','reference_index':1}]}
    with pytest.raises(WorkerError,match='slots disagree'):validate_plan({'shots':[shot]},24,{0,1})

@pytest.fixture
def local_worker_config(tmp_path):
    import sys
    runtime=tmp_path/'private-runtime';(runtime/'custom_nodes'/'ComfyUI-MultiGPU').mkdir(parents=True)
    entry=tmp_path/'installed-comfy-main.py';entry.write_text('# Installed CPU fixture; never executed.\n')
    paths=tmp_path/'model-paths.yaml';paths.write_text('installed_models: {}\n')
    encode=tmp_path/'encode-loader.py';encode.write_text('# Existing original loader\n')
    render=tmp_path/'render-loader.py';render.write_text('# Existing patched single-use loader\n')
    required=tmp_path/'kernel-helper.py';required.write_text('# Existing dependency\n')
    profile={'private_runtime':True,'runtime_directory':str(runtime),'entrypoint':str(entry),'extra_model_paths':str(paths),'whitelist':['ComfyUI-MultiGPU','recastfx_worker_nodes'],'loader_variants':{'encode':str(encode),'render':str(render)},'loader_destination':'custom_nodes/ComfyUI-MultiGPU/distorch_2.py','required_paths':[str(required)]}
    profile_path=tmp_path/'profile.json';profile_path.write_text(json.dumps(profile))
    models={}
    for key in runner.MODEL_KEYS:
        file=tmp_path/f'{key}.safetensors';file.write_bytes(b'Local metadata fixture, not model weights');models[key]=str(file)
    commands={phase:[sys.executable,'-m','self_hosted_worker.launcher','--profile',str(profile_path),'--phase',phase] for phase in ['encode','render']}
    config=runner.Config('https://example.com','paperspace_h3','worker',tmp_path/'spool',commands,models,token='fake-worker-credential')
    return config,profile_path,profile

@pytest.mark.parametrize('route',['google_h3','paperspace_h3'])
def test_readiness_checks_installed_native_and_patched_profiles_without_launch(local_worker_config,monkeypatch,route):
    config,path,profile=local_worker_config;config.route=route
    if route=='google_h3':
        profile.pop('loader_variants');profile.pop('loader_destination');profile['whitelist']=['recastfx_worker_nodes'];path.write_text(json.dumps(profile))
    monkeypatch.setattr(subprocess,'Popen',lambda *_args,**_kwargs:pytest.fail('Local validation must not launch anything'))
    config.validate()

@pytest.mark.parametrize('failure',['missing_profile','missing_entrypoint','shared_runtime','missing_loader','shared_loader_destination','missing_model_paths','missing_helper','unsafe_listener','wrong_phase','wrong_port','paperspace_without_variants'])
def test_bad_local_readiness_cannot_advertise_heartbeat(local_worker_config,monkeypatch,failure):
    config,path,profile=local_worker_config
    if failure=='missing_profile':path.unlink()
    elif failure=='missing_entrypoint':Path(profile['entrypoint']).unlink()
    elif failure=='shared_runtime':profile['runtime_directory']=str(Path(profile['entrypoint']).parent)
    elif failure=='missing_loader':Path(profile['loader_variants']['render']).unlink()
    elif failure=='shared_loader_destination':profile['loader_destination']=str(path.parent/'shared-loader.py')
    elif failure=='missing_model_paths':Path(profile['extra_model_paths']).unlink()
    elif failure=='missing_helper':Path(profile['required_paths'][0]).unlink()
    elif failure=='unsafe_listener':profile['arguments']=['--listen','0.0.0.0']
    elif failure=='wrong_phase':config.launch_commands['render'][-1]='encode'
    elif failure=='wrong_port':config.comfy_url='http://127.0.0.1:8202'
    elif failure=='paperspace_without_variants':profile.pop('loader_variants')
    if failure!='missing_profile':path.write_text(json.dumps(profile))
    class Control:
        def call(self,*_args):pytest.fail('An unready worker must not send a heartbeat')
    monkeypatch.setattr(subprocess,'Popen',lambda *_args,**_kwargs:pytest.fail('Local validation must not start a runtime'))
    with pytest.raises(WorkerError,match='profile'):runner.Worker(config,control=Control()).idle_ready()

def test_removed_profile_stops_idle_capacity_readiness(local_worker_config):
    config,path,_=local_worker_config;config.validate();path.unlink()
    class Control:pass
    assert not runner.Worker(config,control=Control()).capacity_available()
