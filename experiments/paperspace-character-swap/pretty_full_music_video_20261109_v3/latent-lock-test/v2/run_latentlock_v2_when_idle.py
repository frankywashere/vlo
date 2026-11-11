"""Run the isolated latent-lock test when NO batch runner is alive (batch finished or stopped).

Use this instead of queue_shot004_latentlock_v2.py once the main batch is no longer running.
It never signals, cancels or restarts anything, and refuses to start if any runner or
queue controller is alive, the ownership lock is held, or the private Comfy port answers.
"""
from pathlib import Path
import json, os, subprocess, fcntl, hashlib, time, urllib.request

P = Path(__file__).resolve().parent
D = P / 'jobs/pretty_full_music_v3'
PY = '/home/paperspace/ai/ComfyUI/.venv/bin/python'
STATE = P / 'shot004-latentlock_v2-idle-run.json'


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def write(value):
    temp = STATE.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(STATE)


def other_runners():
    found = []
    for d in Path('/proc').iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            cmd = (d / 'cmdline').read_bytes()
        except OSError:
            continue
        if b'offline_runner' in cmd or b'queue_shot004' in cmd:
            found.append(int(d.name))
    return found


with (P / '.latentlock_v2-controller.lock').open('a') as control:
    fcntl.flock(control, fcntl.LOCK_EX | fcntl.LOCK_NB)
    assert not STATE.exists(), 'One idle run only'
    assert not other_runners(), 'A runner or queue controller is alive; use queue_shot004_latentlock_v2.py instead'
    assert not (P / 'cancel.request').exists()
    assert (P / 'profile-latentlock_v2.json').exists() and (P / 'runtime/custom_nodes/pretty_latentlock_v2_nodes/__init__.py').exists(), 'Run stage_latentlock_v2.py first'
    main = json.loads((P / 'journal.json').read_text())
    assert not main.get('launcher'), 'Main journal still records an owned launcher'
    with (P / '.offline.lock').open('a') as ownership:
        fcntl.flock(ownership, fcntl.LOCK_EX | fcntl.LOCK_NB)  # proves nobody owns the runtime; released on exit of this block
        try:
            urllib.request.urlopen('http://127.0.0.1:8203/queue', timeout=2)
        except OSError:
            pass
        else:
            raise RuntimeError('Private runtime still available')
        test_plan = json.loads((P / 'plan-shot004-latentlock_v2.json').read_text())
        assert test_plan['shots'][0]['index'] == 4
        trial = P / 'jobs' / test_plan['job_key']
        trial.mkdir(exist_ok=False)
        for name in ['source.mp4', 'reference-0.image', 'reference-1.image']:
            os.link(D / name, trial / name)
        manifest = json.loads((D / 'job.json').read_text())
        manifest['shots'] = test_plan['shots']
        (trial / 'job.json').write_text(json.dumps(manifest, indent=2))
    record = {'repair_id': 'shot004-latentlock_v2-v1', 'status': 'isolated_latentlock_v2_test_running', 'started_at': time.time(),
              'main_batch_status_at_start': json.loads((P / 'status.json').read_text()).get('status'),
              'main_batch_touched': False, 'paid_api_calls': 0}
    write(record)
    env = dict(os.environ, PYTHONPATH='/home/paperspace/ai/recastfx_site_worker_20261104_v1', OMP_NUM_THREADS='8')
    with (P / 'latentlock_v2-test.log').open('ab', buffering=0) as log:
        result = subprocess.run([PY, '-u', str(P / 'offline_runner_latentlock_v2.py')], stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, env=env, cwd=P)
    output = trial / 'shot-004.mp4'
    record.update(test_exit_code=result.returncode, test_finished_at=time.time())
    if result.returncode == 0 and output.exists():
        record.update(status='completed', test_output_sha256=sha(output))
    else:
        record.update(status='failed_no_automatic_retry')
    write(record)
    print(json.dumps(record, indent=2))
