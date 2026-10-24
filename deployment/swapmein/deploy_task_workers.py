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

import deploy


def resources(preview):
    api = 'recastfx-long-test' if preview else 'recastfx-api'
    worker = 'recastfx-long-worker' if preview else 'recastfx-worker'
    queue = 'recastfx-long-work' if preview else 'recastfx-work'
    module = 'swapmein.long_render_worker:app' if preview else 'swapmein.production_worker:app'
    return api, worker, queue, module


def deploy_worker(image, *, preview):
    if not re.fullmatch(r'us-east4-docker.pkg.dev/charswap-franky-20261013/proof/[a-z0-9-]+@sha256:[a-f0-9]{64}', image):
        raise deploy.SetupError('An immutable image in the existing project repository is required')
    api, worker, queue, module = resources(preview)
    original = deploy.gcloud('run','services','describe',api,'--region='+deploy.REGION)
    spec = original['spec']['template']['spec']
    account = spec['serviceAccountName']
    if any('valueFrom' in entry for entry in spec['containers'][0].get('env', [])):
        raise deploy.SetupError('Inspect secret references before copying the worker configuration')
    env = {item['name']:item['value'] for item in spec['containers'][0].get('env', [])}
    api_url = original['status']['url']
    worker_url = f'https://{worker}-{deploy.PROJECT_NUMBER}.{deploy.REGION}.run.app'
    env.update(SERVICE_URL=api_url,TASK_WORKER_URL=worker_url,RELIABLE_TASKS='true',TASK_WORKER_MODE='true',
               TASK_QUEUE=queue,RENDER_TASK_QUEUE=queue)
    deploy.verify_cap()
    existing_queue = deploy.gcloud('tasks','queues','describe',queue,'--location='+deploy.REGION,optional=True)
    command = 'update' if existing_queue else 'create'
    deploy.gcloud('tasks','queues',command,queue,'--location='+deploy.REGION,
        '--max-concurrent-dispatches=1','--max-dispatches-per-second=1','--max-attempts=20',
        '--max-retry-duration=86400s','--min-backoff=5s','--max-backoff=300s','--max-doublings=6','--log-sampling-ratio=1')
    for role in ('roles/cloudtasks.enqueuer','roles/cloudtasks.viewer'):
        deploy.gcloud('tasks','queues','add-iam-policy-binding',queue,'--location='+deploy.REGION,
            '--member=serviceAccount:'+account,'--role='+role)
    with tempfile.TemporaryDirectory(prefix='recastfx-private-worker-') as directory:
        env_file=Path(directory)/'env.json';env_file.write_text(json.dumps(env))
        body=deploy.gcloud('run','deploy',worker,'--region='+deploy.REGION,'--image='+image,
            '--service-account='+account,'--min-instances=0','--max-instances=1','--concurrency=1',
            '--cpu=2','--memory=4Gi','--timeout=1800','--cpu-throttling','--no-allow-unauthenticated',
            '--env-vars-file='+str(env_file),'--command=python',
            '--args=-m,uvicorn,'+module+',--host,0.0.0.0,--port,8080,--no-access-log',timeout=600)
    actual_url=body['status']['url']
    if actual_url != worker_url:
        worker_url=actual_url
        body=deploy.gcloud('run','services','update',worker,'--region='+deploy.REGION,
            '--update-env-vars=TASK_WORKER_URL='+worker_url,timeout=600)
    # Both Cloud Run's canonical numbered URL and its hashed URL are supported.
    deploy.gcloud('run','services','add-iam-policy-binding',worker,'--region='+deploy.REGION,
        '--member=serviceAccount:'+env['TASK_SERVICE_ACCOUNT'],'--role=roles/run.invoker')
    old_service=deploy.SERVICE
    try:
        deploy.SERVICE=worker;deploy.ensure_gateway_scaling()
    finally:deploy.SERVICE=old_service
    revision=body['status']['latestCreatedRevisionName']
    deploy.gcloud('run','services','update-traffic',worker,'--region='+deploy.REGION,'--to-revisions='+revision+'=100')
    iam=deploy.gcloud('run','services','get-iam-policy',worker,'--region='+deploy.REGION)
    if any(member in {'allUsers','allAuthenticatedUsers'} for binding in iam.get('bindings',[]) for member in binding.get('members',[])):
        raise deploy.SetupError('The task worker must remain private')
    # Preserve every existing API setting except the routing switches. The API
    # no longer performs new heavy tasks; it can handle several light requests.
    body=deploy.gcloud('run','services','update',api,'--region='+deploy.REGION,'--image='+image,'--concurrency=4',
        '--update-env-vars=TASK_WORKER_URL='+worker_url+',RELIABLE_TASKS=true,TASK_WORKER_MODE=false,TASK_QUEUE='+queue+',RENDER_TASK_QUEUE='+queue,
        timeout=600)
    api_revision=body['status']['latestCreatedRevisionName']
    deploy.gcloud('run','services','update-traffic',api,'--region='+deploy.REGION,'--to-revisions='+api_revision+'=100')
    # Scheduler is a bounded dispatcher watchdog; it never calls model APIs.
    enabled=deploy.gcloud('services','list','--enabled','--filter=config.name:cloudscheduler.googleapis.com')
    if not enabled:deploy.gcloud('services','enable','cloudscheduler.googleapis.com')
    scheduler=worker+'-recovery'
    current=deploy.gcloud('scheduler','jobs','describe',scheduler,'--location='+deploy.REGION,optional=True)
    command='update' if current else 'create'
    deploy.gcloud('scheduler','jobs',command,'http',scheduler,'--location='+deploy.REGION,'--schedule=*/5 * * * *',
        '--time-zone=UTC','--uri='+api_url+'/maintenance/recover-deliveries','--http-method=POST','--message-body={}',
        '--headers=Content-Type=application/json','--oidc-service-account-email='+env['TASK_SERVICE_ACCOUNT'],
        '--oidc-token-audience='+api_url,'--attempt-deadline=180s','--max-retry-attempts=0')
    for service in (api,worker):
        current=deploy.google_get(f'https://run.googleapis.com/v2/projects/{deploy.PROJECT}/locations/{deploy.REGION}/services/{service}')
        assert current['scaling'].get('maxInstanceCount')==1
        assert current['template']['scaling'].get('maxInstanceCount')==1
        assert current['template']['containers'][0]['resources'].get('cpuIdle') is True
        assert int(current['scaling'].get('minInstanceCount',0))==0
    return {'api':api,'api_revision':api_revision,'worker':worker,'worker_revision':revision,
            'queue':queue,'scheduler':scheduler,'image':image,'gpu_capacity_changed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment',choices=['preview','production'],required=True)
    parser.add_argument('--image',required=True)
    parser.add_argument('--report',required=True)
    args=parser.parse_args()
    result=deploy_worker(args.image,preview=args.environment=='preview')
    Path(args.report).write_text(json.dumps(result,indent=2))
    print(json.dumps(result))


if __name__=='__main__':main()
