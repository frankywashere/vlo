import importlib.util
import json
from pathlib import Path
import pytest


@pytest.fixture
def release(monkeypatch):
    directory=Path(__file__).parents[1]
    monkeypatch.syspath_prepend(str(directory))
    spec=importlib.util.spec_from_file_location('delivery_release',directory/'deploy_task_workers.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('preview',[True,False])
@pytest.mark.parametrize('existing_scheduler',[True,False])
def test_release_is_scoped_private_bounded_and_preserves_api_settings(release,monkeypatch,preview,existing_scheduler):
    api,worker,queue,_=release.resources(preview)
    calls=[];worker_env={};bounds=[];migrations=[]
    account='existing-task@project.iam.gserviceaccount.com'
    env={'APP_BUCKET':release.deploy.APP_BUCKET,'REAPI_SECRET_NAME':'projects/private/secret-name',
         'TASK_SERVICE_ACCOUNT':account,'AUDIO_URL':'https://proof-audio-63lqoskl5q-uk.a.run.app'}
    def gcloud(*args,**kwargs):
        calls.append(args)
        if args[:4]==('run','services','describe',api):
            return {'spec':{'template':{'spec':{'serviceAccountName':account,'containers':[{'env':[{'name':k,'value':v} for k,v in env.items()]+[{'name':'RECASTFX_JOB_KEY','valueFrom':{'secretKeyRef':{'name':'existing-job-key','key':'latest'}}}]}]}}},
                    'status':{'url':f'https://{api}-63lqoskl5q-uk.a.run.app'}}
        if args[:2]==('run','deploy'):
            service=args[2]
            file=next(arg.split('=',1)[1] for arg in args if arg.startswith('--env-vars-file='))
            worker_env[service]=json.loads(Path(file).read_text())
            return {'status':{'url':f'https://{service}-63lqoskl5q-uk.a.run.app','latestCreatedRevisionName':service+'-new'}}
        if args[:3]==('run','services','update'):
            return {'status':{'url':f'https://{args[3]}-63lqoskl5q-uk.a.run.app','latestCreatedRevisionName':args[3]+'-new'}}
        if args[:3]==('run','services','get-iam-policy'):return {'bindings':[{'members':['serviceAccount:'+account]}]}
        if args[:3]==('services','list','--enabled'):return [{'config':{'name':'cloudscheduler.googleapis.com'}}]
        if args[:3]==('scheduler','jobs','describe'):return {'name':'existing'} if existing_scheduler else None
        if kwargs.get('optional'):return None
        return {}
    monkeypatch.setattr(release.deploy,'gcloud',gcloud)
    monkeypatch.setattr(release.deploy,'verify_cap',lambda:None)
    monkeypatch.setattr(release,'bound_service',lambda service,maximum:bounds.append((service,maximum)))
    monkeypatch.setattr(release,'prepare_capacity_migration',lambda env,**kw:migrations.append(kw))
    monkeypatch.setattr(release.deploy,'google_get',lambda url:{'name':f'projects/{release.deploy.PROJECT}/locations/{release.deploy.REGION}/services/proof-audio',
        'uri':env['AUDIO_URL']})
    result=release.deploy_worker('us-east4-docker.pkg.dev/charswap-franky-20261013/proof/gateway@sha256:'+'a'*64,
                                 preview=preview,submissions_paused=True)
    assert result['gpu_capacity_changed'] is False
    for service,values in worker_env.items():
        assert values['APP_BUCKET']==env['APP_BUCKET']
        assert values['REAPI_SECRET_NAME']==env['REAPI_SECRET_NAME']
        assert values['TASK_QUEUE']==values['RENDER_TASK_QUEUE']==queue
        assert values['TASK_WORKER_MODE']=='true'
        assert 'RECASTFX_JOB_KEY' not in values
    expected={api,worker} if preview else {api,worker,'recastfx-control-worker','proof-audio'}
    writes=[c for c in calls if c[:2]==('run','deploy') or c[:2]==('run','services') and c[2] not in {'describe','get-iam-policy'}]
    assert all((c[2] if c[:2]==('run','deploy') else c[3]) in expected for c in writes)
    queues={c[3]:c for c in calls if c[:3]==('tasks','queues','create')}
    assert '--max-attempts=20' in queues[queue]
    assert '--max-concurrent-dispatches='+('1' if preview else '32') in queues[queue]
    deploy_worker=next(c for c in calls if c[:3]==('run','deploy',worker))
    assert '--min-instances=0' in deploy_worker and '--max-instances='+('1' if preview else '32') in deploy_worker
    assert '--no-allow-unauthenticated' in deploy_worker
    assert '--set-secrets=RECASTFX_JOB_KEY=existing-job-key:latest' in deploy_worker
    assert not any('gpu' in item.lower() for c in calls for item in c)
    scheduler_call=next(c for c in calls if c[:2]==('scheduler','jobs') and c[2] in {'create','update'})
    assert ('--update-headers=Content-Type=application/json' if existing_scheduler else '--headers=Content-Type=application/json') in scheduler_call
    assert any(item.endswith('/maintenance/recover-deliveries') for item in scheduler_call)
    if preview:
        assert not migrations and not result['control_worker']
        assert set(worker_env)=={worker} and ('proof-audio',32) not in bounds
    else:
        assert migrations==[{'submissions_paused':True}]
        assert '--max-concurrent-dispatches=16' in queues['recastfx-control']
        control=next(c for c in calls if c[:3]==('run','deploy','recastfx-control-worker'))
        assert '--max-instances=2' in control and '--concurrency=8' in control
        assert worker_env[worker]['TASK_WORKER_LANE']=='heavy'
        assert worker_env['recastfx-control-worker']['TASK_WORKER_LANE']=='control'
        assert worker_env[worker]['REQUEST_SCALED_AUDIO']=='true'
        assert worker_env[worker]['PROCESSING_CAPACITY_ENABLED']=='false'  # Activated only after canonical routing exists.
        assert ('proof-audio',32) in bounds
        api_update=next(c for c in calls if c[:4]==('run','services','update',api))
        assert any('PROCESSING_CAPACITY_ENABLED=true' in arg for arg in api_update)


def test_service_scaling_is_etag_guarded_scoped_and_verified(release,monkeypatch):
    resource=f'projects/{release.deploy.PROJECT}/locations/{release.deploy.REGION}/services/recastfx-worker'
    state={'name':resource,'etag':'original','generation':'1','observedGeneration':'1',
           'terminalCondition':{'state':'CONDITION_SUCCEEDED'},'scaling':{'maxInstanceCount':1},
           'template':{'scaling':{'maxInstanceCount':32},'containers':[{'resources':{'cpuIdle':True}}]}}
    updates=[]
    monkeypatch.setattr(release.deploy,'google_get',lambda url:state)
    def patch(url,body):
        updates.append((url,body));state['scaling']=body['scaling']
        return {'name':f'projects/{release.deploy.PROJECT}/locations/{release.deploy.REGION}/operations/safe','done':True}
    monkeypatch.setattr(release.deploy,'google_patch',patch)
    release.bound_service('recastfx-worker',32)
    assert updates[0][1]['etag']=='original'
    assert updates[0][1]['scaling']=={'minInstanceCount':0,'maxInstanceCount':32,'scalingMode':'AUTOMATIC'}
    with pytest.raises(release.deploy.SetupError):release.bound_service('proof-gpu',32)


def test_migration_requires_an_explicit_admission_pause_before_access(release,monkeypatch):
    monkeypatch.setattr(release.deploy,'gcloud',lambda *args,**kw:pytest.fail('No credentials before pause confirmation'))
    with pytest.raises(release.deploy.SetupError,match='Pause new submissions'):
        release.prepare_capacity_migration({},submissions_paused=False)


@pytest.mark.parametrize('preparation,pending,allowed',[
    ({'active':{'job_id':'a'*32}},[],False),
    ({},[{'name':'projects/test/queues/proof-shutdown/tasks/shutdown-audio-old'}],False),
    ({},[],True),
])
def test_capacity_migration_never_overrides_active_legacy_cpu_work(release,monkeypatch,preparation,pending,allowed):
    from google.cloud import storage
    from google.oauth2 import credentials
    import swapmein.storage
    import swapmein.processing_capacity
    calls=[]
    class Records:
        def __init__(self,*args):pass
        def read(self,path):return preparation,1
    monkeypatch.setattr(storage,'Client',lambda **kw:object())
    monkeypatch.setattr(credentials,'Credentials',lambda token:object())
    monkeypatch.setattr(swapmein.storage,'Records',Records)
    monkeypatch.setattr(swapmein.processing_capacity,'initialize_capacity',lambda records:calls.append('initialized'))
    monkeypatch.setattr(release.deploy,'gcloud',lambda *args,**kw:'test-token' if args[0]=='auth' else pending)
    if allowed:
        release.prepare_capacity_migration({'APP_BUCKET':release.deploy.APP_BUCKET},submissions_paused=True)
        assert calls==['initialized']
    else:
        with pytest.raises(release.deploy.SetupError):
            release.prepare_capacity_migration({'APP_BUCKET':release.deploy.APP_BUCKET},submissions_paused=True)
        assert not calls
