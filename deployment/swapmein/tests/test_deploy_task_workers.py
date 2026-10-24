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
    calls=[];worker_env=[]
    account='existing-task@project.iam.gserviceaccount.com'
    env={'APP_BUCKET':'private-existing-bucket','REAPI_SECRET_NAME':'projects/private/secret-name',
         'TASK_SERVICE_ACCOUNT':account,'AUDIO_URL':'https://existing-private-cpu'}
    def gcloud(*args,**kwargs):
        calls.append(args)
        if args[:4]==('run','services','describe',api):
            return {'spec':{'template':{'spec':{'serviceAccountName':account,'containers':[{'env':[{'name':k,'value':v} for k,v in env.items()]+[{'name':'RECASTFX_JOB_KEY','valueFrom':{'secretKeyRef':{'name':'existing-job-key','key':'latest'}}}]}]}}},
                    'status':{'url':f'https://{api}-63lqoskl5q-uk.a.run.app'}}
        if args[:3]==('run','deploy',worker):
            file=next(arg.split('=',1)[1] for arg in args if arg.startswith('--env-vars-file='))
            worker_env.append(json.loads(Path(file).read_text()))
            return {'status':{'url':f'https://{worker}-63lqoskl5q-uk.a.run.app','latestCreatedRevisionName':worker+'-new'}}
        if args[:3]==('run','services','update'):
            return {'status':{'url':f'https://{args[3]}-63lqoskl5q-uk.a.run.app','latestCreatedRevisionName':args[3]+'-new'}}
        if args[:3]==('run','services','get-iam-policy'):return {'bindings':[{'members':['serviceAccount:'+account]}]}
        if args[:3]==('services','list','--enabled'):return [{'config':{'name':'cloudscheduler.googleapis.com'}}]
        if args[:3]==('scheduler','jobs','describe'):return {'name':'existing'} if existing_scheduler else None
        if kwargs.get('optional'):return None
        return {}
    monkeypatch.setattr(release.deploy,'gcloud',gcloud)
    monkeypatch.setattr(release.deploy,'verify_cap',lambda:None)
    monkeypatch.setattr(release.deploy,'ensure_gateway_scaling',lambda:None)
    monkeypatch.setattr(release.deploy,'google_get',lambda url:{'scaling':{'maxInstanceCount':1},
        'template':{'scaling':{'maxInstanceCount':1},'containers':[{'resources':{'cpuIdle':True}}]}})
    result=release.deploy_worker('us-east4-docker.pkg.dev/charswap-franky-20261013/proof/gateway@sha256:'+'a'*64,preview=preview)
    assert result['gpu_capacity_changed'] is False
    assert worker_env[0]['APP_BUCKET']==env['APP_BUCKET']
    assert worker_env[0]['REAPI_SECRET_NAME']==env['REAPI_SECRET_NAME']
    assert worker_env[0]['TASK_QUEUE']==worker_env[0]['RENDER_TASK_QUEUE']==queue
    assert worker_env[0]['TASK_WORKER_MODE']=='true'
    writes=[c for c in calls if c[:2]==('run','deploy') or c[:2]==('run','services') and c[2]!='describe']
    assert all((c[2] if c[:2]==('run','deploy') else c[3]) in {api,worker} for c in writes)
    queue_create=next(c for c in calls if c[:3]==('tasks','queues','create'))
    assert '--max-attempts=20' in queue_create and '--max-concurrent-dispatches=1' in queue_create
    deploy_worker=next(c for c in calls if c[:2]==('run','deploy'))
    assert '--min-instances=0' in deploy_worker and '--max-instances=1' in deploy_worker and '--no-allow-unauthenticated' in deploy_worker
    assert not any('gpu' in item.lower() for c in calls for item in c)

    scheduler_call=next(c for c in calls if c[:2]==('scheduler','jobs') and c[2] in {'create','update'})
    assert ('--update-headers=Content-Type=application/json' if existing_scheduler else '--headers=Content-Type=application/json') in scheduler_call
    assert any(item.endswith('/maintenance/recover-deliveries') for item in scheduler_call)

    assert '--set-secrets=RECASTFX_JOB_KEY=existing-job-key:latest' in deploy_worker
    assert 'RECASTFX_JOB_KEY' not in worker_env[0]
