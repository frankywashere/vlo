from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from google.api_core.exceptions import AlreadyExists, NotFound
import pytest
from swapmein.config import Settings
from swapmein.errors import PublicError
from swapmein.task_delivery import TaskDelivery, LEASE_SECONDS
from swapmein.task_worker import create_task_worker, recovery_before_work, register_delivery_recovery

JOB, TOKEN = 'a' * 32, 'b' * 32

class Records:
    def __init__(self): self.data = {}
    def read(self, path): return deepcopy(self.data.get(path)), 1
    def update(self, path, mutate):
        value = mutate(deepcopy(self.data.get(path)))
        if value is not None: self.data[path] = deepcopy(value)
        return deepcopy(value)
    def delete(self, path): self.data.pop(path, None)
    def list_paths_page(self, prefix, maximum=50, cursor=None):
        keys = sorted(k for k in self.data if k.startswith(prefix))
        start = int(cursor or 0); stop = start + maximum
        return keys[start:stop], str(stop) if stop < len(keys) else None

class Tasks:
    def __init__(self): self.tasks = {}; self.posts = 0; self.lose_ack = False
    def task_path(self, project, region, queue, name): return f'{queue}/{name}'
    def queue_path(self, project, region, queue): return queue
    def create_task(self, parent, task, **options):
        if task.name in self.tasks: raise AlreadyExists('accepted')
        self.tasks[task.name] = task; self.posts += 1
        if self.lose_ack: raise TimeoutError('lost ack')
        return task
    def get_task(self, name, **options):
        if name not in self.tasks: raise NotFound('gone')
        return self.tasks[name]

@pytest.fixture
def runtime(monkeypatch):
    clock = [10000.]
    monkeypatch.setattr('swapmein.task_delivery.time.time', lambda: clock[0])
    settings = replace(Settings(), app_bucket='charswap-franky-20261013-long-test',
        service_url='https://recastfx-long-test-63lqoskl5q-uk.a.run.app',
        task_worker_url='https://recastfx-long-worker-63lqoskl5q-uk.a.run.app',
        task_queue='recastfx-long-work', render_task_queue='recastfx-long-work',
        reliable_tasks=True, task_worker_mode=True, task_service_account='task@example.com')
    records, tasks = Records(), Tasks()
    return settings, records, tasks, clock, TaskDelivery(settings, records, tasks)


def test_busy_dispatch_waits_for_delivery_and_completed_repeat_does_not_execute(runtime, monkeypatch):
    settings, records, tasks, _, delivery = runtime
    delivery.enqueue('long-assemble', JOB, task_token=TOKEN)
    task = next(iter(tasks.tasks.values())); body = json.loads(task.http_request.body)
    assert task.http_request.url.startswith(settings.task_worker_url)
    assert task.http_request.oidc_token.audience == settings.task_worker_url
    assert task.schedule_time.timestamp() >= 10005
    records.data['jobs/' + JOB + '.json'] = {'id':JOB,'status':'finalizing'}
    source = FastAPI(); executed = []
    @source.post('/internal/long-assemble')
    def assemble(): executed.append(JOB); return {'ok':True}
    @source.get('/api/account/jobs')
    def public(): pytest.fail('Public API exposed by worker')
    monkeypatch.setattr('swapmein.task_worker.verify_task_identity',lambda *args:None)
    app = create_task_worker(source,lambda:(settings,records,SimpleNamespace(tasks=tasks)))
    with TestClient(app) as client:
        assert client.post('/internal/long-assemble',json=body).status_code == 200
        assert client.post('/internal/long-assemble',json=body).json()['duplicate']
        assert client.get('/api/account/jobs').status_code == 404
    assert executed == [JOB]
    assert not any(k.startswith('task-active/') for k in records.data)


def test_lost_create_ack_is_deduplicated(runtime):
    _, _, tasks, clock, delivery = runtime
    tasks.lose_ack = True
    delivery.enqueue('long-generate',JOB,task_token=TOKEN)
    delivery.enqueue('long-generate',JOB,task_token=TOKEN)
    assert tasks.posts == 1
    clock[0] += 100
    assert delivery.sweep()['requeued'] == 0


def test_exhausted_dispatch_is_recovered_with_a_bound(runtime):
    _, records, tasks, clock, delivery = runtime
    delivery.enqueue('long-assemble',JOB,task_token=TOKEN)
    records.data['jobs/'+JOB+'.json']={'status':'finalizing'}
    for i in range(2):
        tasks.tasks.clear();clock[0]+=100
        assert delivery.sweep()['requeued']==1
    tasks.tasks.clear();clock[0]+=100
    assert delivery.sweep()['blocked']==1
    assert tasks.posts==3
    assert records.data['jobs/'+JOB+'.json']['queue_recovery_status']=='required'


def test_active_lease_prevents_duplicate_work_and_watchdog_dispatch(runtime):
    _, _, tasks, clock, delivery = runtime
    delivery.enqueue('long-assemble',JOB,task_token=TOKEN)
    key, claim, receipt, acquired = delivery.begin('long-assemble',JOB,TOKEN)
    assert acquired
    with pytest.raises(PublicError,match='already running'): delivery.begin('long-assemble',JOB,TOKEN)
    tasks.tasks.clear();clock[0]+=100
    assert delivery.sweep()['requeued']==0
    delivery.finish(key,claim,successful=True)
    assert delivery.sweep()['requeued']==0


def test_semantic_none_token_is_preserved(runtime):
    _, _, tasks, _, delivery = runtime
    delivery.enqueue('prepare',JOB)
    body=json.loads(next(iter(tasks.tasks.values())).http_request.body)
    assert body['task_token'] is None and len(body['delivery_token'])==32


@pytest.mark.parametrize('state,kind,expected', [('assembling','long-assemble','finalizing'),('finishing','finish','finalizing'),
    ('preparing','prepare','failed'),('reviewing','long-review','failed'),('submitting','generate','submission_unknown')])
def test_interrupted_work_never_blindly_repeats_paid_requests(runtime,state,kind,expected):
    settings,records,_,clock,_=runtime
    records.data['jobs/'+JOB+'.json']={'status':state,'updated_at':clock[0]-LEASE_SECONDS-1}
    result=recovery_before_work(settings,records,kind,JOB,{'executions':2})
    assert records.data['jobs/'+JOB+'.json']['status']==expected
    assert result==(expected=='finalizing')


def test_bad_identity_rejected_before_records_or_model(runtime,monkeypatch):
    settings,records,tasks,_,_=runtime
    def reject(*args):raise PublicError('no',401)
    monkeypatch.setattr('swapmein.task_worker.verify_task_identity',reject)
    app=create_task_worker(FastAPI(),lambda:(settings,records,SimpleNamespace(tasks=tasks)))
    with TestClient(app) as client:assert client.post('/internal/long-assemble',json={'job_id':JOB}).status_code==401
    assert not records.data and tasks.posts==0


def test_preview_cannot_dispatch_to_production(runtime):
    settings,records,tasks,_,_=runtime
    with pytest.raises(PublicError,match='configured'):
        TaskDelivery(replace(settings,task_worker_url='https://recastfx-worker-63lqoskl5q-uk.a.run.app'),records,tasks).enqueue('long-assemble',JOB)
    assert tasks.posts==0


def test_watchdog_authenticates_without_paid_calls(runtime,monkeypatch):
    settings,records,tasks,_,_=runtime; authenticated=[]
    monkeypatch.setattr('swapmein.task_worker.verify_task_identity',lambda *args:authenticated.append(args))
    app=FastAPI();register_delivery_recovery(app,lambda:(settings,records,SimpleNamespace(tasks=tasks)))
    with TestClient(app) as client:assert client.post('/internal/recover-deliveries',headers={'Authorization':'Bearer scheduler'}).status_code==200
    assert authenticated[0][1]==settings.service_url and tasks.posts==0


def test_active_pointer_survives_receipt_write_interruption(runtime):
    _, records, tasks, clock, delivery = runtime
    delivery.enqueue('long-assemble',JOB,task_token=TOKEN)
    for key in list(records.data):
        if key.startswith('task-delivery/'): del records.data[key]
    tasks.tasks.clear();clock[0]+=100
    assert delivery.sweep()['requeued']==1
    assert tasks.posts==2


@pytest.mark.parametrize('kind',['admit','prepare','generate','poll-provider','finish','long-prepare','long-review','long-generate','long-poll','long-assemble','long-comparison'])
def test_every_background_phase_uses_the_capacity_limited_private_queue(runtime,kind):
    _,_,tasks,_,delivery=runtime
    delivery.enqueue(kind,JOB,task_token=TOKEN)
    task=next(iter(tasks.tasks.values()))
    assert task.name.startswith('recastfx-long-work/')
    assert task.http_request.url.endswith('/internal/'+kind)


def test_finishing_lease_does_not_reset_while_active(runtime):
    settings,records,_,clock,_=runtime
    records.data['jobs/'+JOB+'.json']={'status':'assembling','updated_at':clock[0]}
    with pytest.raises(PublicError,match='lease'):
        recovery_before_work(settings,records,'long-assemble',JOB,{'executions':2})
    assert records.data['jobs/'+JOB+'.json']['status']=='assembling'


def test_completed_receipt_cannot_be_reopened_after_lost_finish_ack(runtime):
    _,_,_,_,delivery=runtime
    delivery.enqueue('long-generate',JOB,task_token=TOKEN)
    key,claim,_,_=delivery.begin('long-generate',JOB,TOKEN)
    delivery.finish(key,claim,successful=True)
    delivery.finish(key,claim,successful=False)
    assert delivery.begin('long-generate',JOB,TOKEN)[3] is False
