#!/usr/bin/env python3
"""Install private CPU workers and durable delivery without changing GPU capacity.

Deploy an already-tested immutable image. Preview and production have separate
workers, queues, records and watchdog jobs; no user/provider key is copied.
"""
import argparse
import json
from pathlib import Path
import re
import tempfile
import time

import deploy


def resources(preview):
    api = 'recastfx-long-test' if preview else 'recastfx-api'
    worker = 'recastfx-long-worker' if preview else 'recastfx-worker'
    queue = 'recastfx-long-work' if preview else 'recastfx-work'
    module = 'swapmein.long_render_worker:app' if preview else 'swapmein.production_worker:app'
    return api, worker, queue, module


def bound_service(service, maximum):
    """Apply only service-level bounds; revision bounds came from deployment."""
    allowed = {'recastfx-long-test': 1, 'recastfx-api': 1, 'recastfx-long-worker': 1,
               'recastfx-worker': 4, 'recastfx-control-worker': 2, 'proof-audio': 4}
    if allowed.get(service) != maximum:
        raise deploy.SetupError('Unexpected worker scaling target')
    resource = f'projects/{deploy.PROJECT}/locations/{deploy.REGION}/services/{service}'
    url = 'https://run.googleapis.com/v2/' + resource
    deadline = time.monotonic() + 180
    current = deploy.google_get(url)
    if (current.get('name') != resource or current.get('reconciling') or current.get('deleteTime')
            or current.get('invokerIamDisabled') is True):
        raise deploy.SetupError('Worker identity/readiness changed; inspect before scaling')
    if int(current.get('template', {}).get('scaling', {}).get('maxInstanceCount', 0)) != maximum:
        raise deploy.SetupError('Worker revision scaling must match the bounded service limit')
    scaling = current.get('scaling', {})
    if (int(scaling.get('minInstanceCount', 0)) != 0 or int(scaling.get('maxInstanceCount', 0)) != maximum
            or scaling.get('scalingMode', 'AUTOMATIC') != 'AUTOMATIC'):
        if not current.get('etag'):
            raise deploy.SetupError('Worker ETag was absent; refusing an unguarded scaling update')
        operation = deploy.google_patch(url+'?updateMask=scaling', {'name': resource, 'etag': current['etag'],
            'scaling': {'minInstanceCount': 0, 'maxInstanceCount': maximum, 'scalingMode': 'AUTOMATIC'}})
        prefix = f'projects/{deploy.PROJECT}/locations/{deploy.REGION}/operations/'
        if not re.fullmatch(re.escape(prefix)+r'[A-Za-z0-9_-]+', operation.get('name', '')):
            raise deploy.SetupError('Unexpected worker scaling operation')
        while not operation.get('done'):
            if time.monotonic() >= deadline:
                raise deploy.SetupError('Worker scaling timed out; inspect before retrying')
            time.sleep(2)
            operation = deploy.google_get('https://run.googleapis.com/v2/'+operation['name'])
        if operation.get('error'):
            raise deploy.SetupError('Worker scaling operation failed')
    while True:
        current = deploy.google_get(url)
        if not current.get('reconciling'):
            break
        if time.monotonic() >= deadline:
            raise deploy.SetupError('Worker scaling readiness timed out')
        time.sleep(2)
    scaling, revision = current.get('scaling', {}), current.get('template', {}).get('scaling', {})
    if (current.get('name') != resource or int(scaling.get('minInstanceCount', 0)) != 0
            or int(scaling.get('maxInstanceCount', 0)) != maximum
            or scaling.get('scalingMode', 'AUTOMATIC') != 'AUTOMATIC'
            or int(revision.get('minInstanceCount', 0)) != 0
            or int(revision.get('maxInstanceCount', 0)) != maximum
            or current.get('terminalCondition', {}).get('state') != 'CONDITION_SUCCEEDED'
            or current.get('generation') is None
            or str(current.get('generation')) != str(current.get('observedGeneration'))
            or current.get('template', {}).get('containers', [{}])[0].get('resources', {}).get('cpuIdle') is not True):
        raise deploy.SetupError('Worker bounded request-billed scaling was not confirmed')
    return current


def prepare_capacity_migration(env, *, submissions_paused):
    """Fail closed until admission is paused and old CPU shutdowns have drained."""
    if not submissions_paused:
        raise deploy.SetupError('Pause new submissions before the concurrency migration, then use --submissions-paused')
    if env.get('APP_BUCKET') != deploy.APP_BUCKET:
        raise deploy.SetupError('Production capacity migration requires the existing application bucket')
    from google.cloud import storage
    from google.oauth2.credentials import Credentials
    from swapmein.storage import Records
    from swapmein.processing_capacity import initialize_capacity
    client = storage.Client(project=deploy.PROJECT,
        credentials=Credentials(deploy.gcloud('auth','print-access-token',json_output=False)))
    records = Records(env['APP_BUCKET'], client)
    preparation, _ = records.read('control/preparation.json')
    if (preparation or {}).get('active') or (preparation or {}).get('active_jobs'):
        raise deploy.SetupError('A legacy preparation is still active; wait or recover it before changing CPU scaling')
    pending = deploy.gcloud('tasks','list','--queue=proof-shutdown','--location='+deploy.REGION,'--limit=1000')
    if len(pending) >= 1000 or any('/shutdown-audio-' in item.get('name', '') for item in pending):
        raise deploy.SetupError('Legacy CPU shutdown tasks must drain before enabling automatic CPU scaling')
    return initialize_capacity(records)


def deploy_worker(image, *, preview, submissions_paused=False):
    if not re.fullmatch(r'us-east4-docker.pkg.dev/charswap-franky-20261013/proof/[a-z0-9-]+@sha256:[a-f0-9]{64}', image):
        raise deploy.SetupError('An immutable image in the existing project repository is required')
    api, worker, queue, module = resources(preview)
    original = deploy.gcloud('run','services','describe',api,'--region='+deploy.REGION)
    spec = original['spec']['template']['spec']
    account = spec['serviceAccountName']
    secret_refs = []
    for entry in spec['containers'][0].get('env', []):
        if 'valueFrom' not in entry:
            continue
        ref = entry['valueFrom'].get('secretKeyRef', {})
        if (not re.fullmatch(r'[A-Z][A-Z0-9_]*', entry['name'])
                or not re.fullmatch(r'[a-zA-Z0-9_-]+', ref.get('name', ''))
                or not re.fullmatch(r'latest|[0-9]+', ref.get('key', ''))):
            raise deploy.SetupError('Unsupported secret reference; inspect before copying the worker configuration')
        secret_refs.append(entry['name']+'='+ref['name']+':'+ref['key'])
    secret_args = ['--set-secrets='+','.join(secret_refs)] if secret_refs else []
    env = {item['name']:item['value'] for item in spec['containers'][0].get('env', []) if 'value' in item}
    api_url = original['status']['url']
    worker_url = f'https://{worker}-{deploy.PROJECT_NUMBER}.{deploy.REGION}.run.app'
    control_worker, control_queue = 'recastfx-control-worker', 'recastfx-control'
    control_url = '' if preview else f'https://{control_worker}-{deploy.PROJECT_NUMBER}.{deploy.REGION}.run.app'
    env.update(SERVICE_URL=api_url,TASK_WORKER_URL=worker_url,RELIABLE_TASKS='true',TASK_WORKER_MODE='true',
               TASK_QUEUE=queue,RENDER_TASK_QUEUE=queue,CONTROL_TASK_WORKER_URL=control_url,
               CONTROL_TASK_QUEUE='' if preview else control_queue,TASK_WORKER_LANE='heavy')
    deploy.verify_cap()
    if not preview:
        prepare_capacity_migration(env, submissions_paused=submissions_paused)
        cpu = deploy.google_get(f'https://run.googleapis.com/v2/projects/{deploy.PROJECT}/locations/{deploy.REGION}/services/proof-audio')
        expected_cpu = f'projects/{deploy.PROJECT}/locations/{deploy.REGION}/services/proof-audio'
        numbered_cpu = f'https://proof-audio-{deploy.PROJECT_NUMBER}.{deploy.REGION}.run.app'
        if (cpu.get('name') != expected_cpu or cpu.get('invokerIamDisabled') is True
                or env.get('AUDIO_URL') not in {cpu.get('uri'), numbered_cpu}):
            raise deploy.SetupError('The existing private CPU endpoint could not be verified')
        cpu_iam = deploy.gcloud('run','services','get-iam-policy','proof-audio','--region='+deploy.REGION)
        if any(member in {'allUsers','allAuthenticatedUsers'} for binding in cpu_iam.get('bindings', []) for member in binding.get('members', [])):
            raise deploy.SetupError('The existing CPU worker must remain private')
        # The old per-request manual zeroing could stop another user's guide.
        # Keep the existing image/identity/private IAM, and let request-based
        # autoscaling own this CPU service before enabling parallel preparation.
        deploy.gcloud('run','services','update','proof-audio','--region='+deploy.REGION,
            '--min-instances=0','--max-instances=4','--concurrency=1','--cpu-throttling',timeout=600)
        bound_service('proof-audio',4)
        env['REQUEST_SCALED_AUDIO']='true'
        env['PROCESSING_CAPACITY_ENABLED']='false'
    lanes = [('heavy',worker,queue,1 if preview else 4,1)]
    if not preview:
        lanes.append(('control',control_worker,control_queue,2,8))
    revisions, urls = {}, {}
    for lane, service, lane_queue, maximum, concurrency in lanes:
        existing_queue = deploy.gcloud('tasks','queues','describe',lane_queue,'--location='+deploy.REGION,optional=True)
        command = 'update' if existing_queue else 'create'
        dispatches = maximum * concurrency
        deploy.gcloud('tasks','queues',command,lane_queue,'--location='+deploy.REGION,
            '--max-concurrent-dispatches='+str(dispatches),'--max-dispatches-per-second='+str(dispatches),
            '--max-attempts=20','--max-retry-duration=86400s','--min-backoff=5s','--max-backoff=300s',
            '--max-doublings=6','--log-sampling-ratio=1')
        for role in ('roles/cloudtasks.enqueuer','roles/cloudtasks.viewer'):
            deploy.gcloud('tasks','queues','add-iam-policy-binding',lane_queue,'--location='+deploy.REGION,
                '--member=serviceAccount:'+account,'--role='+role)
        lane_env = {**env,'TASK_WORKER_LANE':lane}
        with tempfile.TemporaryDirectory(prefix='recastfx-private-worker-') as directory:
            env_file=Path(directory)/'env.json';env_file.write_text(json.dumps(lane_env))
            body=deploy.gcloud('run','deploy',service,'--region='+deploy.REGION,'--image='+image,
                '--service-account='+account,'--min-instances=0','--max-instances='+str(maximum),
                '--concurrency='+str(concurrency),'--cpu=2','--memory=4Gi','--timeout=1800',
                '--cpu-throttling','--no-allow-unauthenticated','--env-vars-file='+str(env_file),
                '--command=python','--args=-m,uvicorn,'+module+',--host,0.0.0.0,--port,8080,--no-access-log',
                *secret_args,timeout=600)
        urls[lane] = body['status']['url']
        revisions[lane] = body['status']['latestCreatedRevisionName']
        deploy.gcloud('run','services','add-iam-policy-binding',service,'--region='+deploy.REGION,
            '--member=serviceAccount:'+env['TASK_SERVICE_ACCOUNT'],'--role=roles/run.invoker')
        bound_service(service,maximum)
        iam=deploy.gcloud('run','services','get-iam-policy',service,'--region='+deploy.REGION)
        if any(member in {'allUsers','allAuthenticatedUsers'} for binding in iam.get('bindings',[]) for member in binding.get('members',[])):
            raise deploy.SetupError('The task worker must remain private')
    worker_url, control_url = urls['heavy'], urls.get('control','')
    common = ('TASK_WORKER_URL='+worker_url+',CONTROL_TASK_WORKER_URL='+control_url+
              ',CONTROL_TASK_QUEUE='+('' if preview else control_queue)+
              ('' if preview else ',PROCESSING_CAPACITY_ENABLED=true'))
    # Resolve canonical URLs before either worker receives new instructions.
    for lane, service, _, _, _ in lanes:
        body=deploy.gcloud('run','services','update',service,'--region='+deploy.REGION,
            '--update-env-vars='+common,timeout=600)
        revisions[lane]=body['status']['latestCreatedRevisionName']
        deploy.gcloud('run','services','update-traffic',service,'--region='+deploy.REGION,
            '--to-revisions='+revisions[lane]+'=100')
    api_env = (common+',RELIABLE_TASKS=true,TASK_WORKER_MODE=false,TASK_WORKER_LANE=heavy,TASK_QUEUE='+queue+
               ',RENDER_TASK_QUEUE='+queue+('' if preview else ',REQUEST_SCALED_AUDIO=true'))
    body=deploy.gcloud('run','services','update',api,'--region='+deploy.REGION,'--image='+image,'--concurrency=4',
        '--update-env-vars='+api_env,timeout=600)
    api_revision=body['status']['latestCreatedRevisionName']
    deploy.gcloud('run','services','update-traffic',api,'--region='+deploy.REGION,'--to-revisions='+api_revision+'=100')
    enabled=deploy.gcloud('services','list','--enabled','--filter=config.name:cloudscheduler.googleapis.com')
    if not enabled:deploy.gcloud('services','enable','cloudscheduler.googleapis.com')
    scheduler=worker+'-recovery'
    current=deploy.gcloud('scheduler','jobs','describe',scheduler,'--location='+deploy.REGION,optional=True)
    command='update' if current else 'create'
    deploy.gcloud('scheduler','jobs',command,'http',scheduler,'--location='+deploy.REGION,'--schedule=*/5 * * * *',
        '--time-zone=UTC','--uri='+api_url+'/maintenance/recover-deliveries','--http-method=POST','--message-body={}',
        ('--update-headers=' if current else '--headers=')+'Content-Type=application/json',
        '--oidc-service-account-email='+env['TASK_SERVICE_ACCOUNT'],
        '--oidc-token-audience='+api_url,'--attempt-deadline=180s','--max-retry-attempts=0')
    bound_service(api,1)
    return {'api':api,'api_revision':api_revision,'worker':worker,'worker_revision':revisions['heavy'],
            'control_worker':None if preview else control_worker,'control_worker_revision':revisions.get('control'),
            'queue':queue,'control_queue':None if preview else control_queue,'scheduler':scheduler,'image':image,
            'gpu_capacity_changed':False,'request_scaled_audio':not preview}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',choices=['preview','production'],required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--report',required=True)
    parser.add_argument('--submissions-paused',action='store_true',
        help='Confirm production admission is paused for the one-time capacity migration')
    args=parser.parse_args()
    result=deploy_worker(args.image,preview=args.environment=='preview',submissions_paused=args.submissions_paused)
    Path(args.report).write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
