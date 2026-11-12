"""Bounded mixed-load campaign; all failures and pre-teardown state are archived."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time

from campaign import ROOT, command, write, utc, app_request, inspect_state, container_state, queues_empty, wait_workers, archive, snapshot, seal_archive

WORKERS = ['bench-dispatcher', 'bench-search', 'bench-playback']


def state(action, seeds, expected=None, fixture=None, stopped=False):
    args = ['docker', 'exec', 'bench-api', 'python', '/bench/mixed_state.py', action, '--seeds', json.dumps(seeds)]
    if expected is not None: args += ['--expected', json.dumps(expected)]
    if fixture: args += ['--sha256', fixture['sha256'], '--bytes', str(fixture['bytes'])]
    if stopped: args += ['--writers-stopped']
    return json.loads(command(args, 300))


def stop_workers():
    # Stop new dispatch first. The idle dispatcher can retain Celery signal
    # handlers; it does not need the processing workers' full drain allowance.
    command(['docker', 'stop', '-t', '5', 'bench-dispatcher'], 30)
    command(['docker', 'stop', '-t', '60', 'bench-search', 'bench-playback'], 150)
    assert all(not container_state()[name]['running'] for name in WORKERS)


def summary(seeds):
    # Sample via Postgres, avoiding Python/model startup inside the measured API quota.
    from uuid import UUID
    safe = ','.join("'"+str(UUID(value))+"'" for value in seeds)
    sql = f"""SELECT json_build_object('epoch',extract(epoch from now()),
      'videos',count(*),'searchable',count(*) FILTER (WHERE embedding_status='completed'),
      'playable',count(*) FILTER (WHERE status='processed' AND processed_path IS NOT NULL),
      'pending_videos',count(*) FILTER (WHERE status<>'processed' OR embedding_status<>'completed'),
      'oldest_pending_seconds',coalesce(max(extract(epoch from now()-created_at)) FILTER
        (WHERE status<>'processed' OR embedding_status<>'completed'),0))
      FROM videos WHERE video_id NOT IN ({safe});"""
    return json.loads(command(['docker', 'exec', 'bench-postgres', 'psql', '-U', 'bench', '-d', 'vindex_pipeline_bench', '-Atc', sql]))


def seed():
    settings = json.loads((ROOT/'run-settings.json').read_text())
    for _ in range(60):
        try:
            if app_request('/health', timeout=5).get('status') == 'ok': break
        except Exception: pass
        time.sleep(2)
    wait_workers()
    assert app_request('/bench/pipeline/warm-query', 'POST', 120)['query_model_warmed']
    ids = json.loads(command(['docker', 'exec', 'bench-api', 'python', '/bench/mixed_seed.py'], 600))
    write(ROOT/'results/seeds.json', {'ids': ids, 'created_at': utc()}); archive(ROOT/'results/seeds.json')
    for _ in range(180):
        observation = inspect_state()
        if observation['active'] == 0 and all(v['embedding_status'] == 'completed' and v['status'] == 'processed' for v in observation['videos']): break
        time.sleep(2)
    fixture = ROOT/'fixtures/speech-20s.mp4'
    verification = inspect_state('verify', ids, {'bytes': fixture.stat().st_size, 'sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()})
    write(ROOT/'results/seed-verification.json', verification)
    stop_workers(); command(['docker', 'stop', 'bench-browser'])
    archive(); seal_archive('seeded')


def trial(folder, spec, settings):
    seeds = spec['seeds']; bucket = settings['bucket']
    folder.mkdir()
    assert state('summary', seeds)['videos'] == 0 and not any(queues_empty().values()), 'Previous trial is not clean'
    if 'architecture' in spec:
        assert spec['architecture'] in ('serialized', 'branches', 'dag') and spec['processing'] == 'active'
        stop_workers()
        write(ROOT/'architecture.json', {'architecture': spec['architecture']})
        command(['docker', 'start', 'bench-search'])
        warm = json.loads(command(['docker', 'exec', 'bench-search', 'python', '/bench/architecture_warm.py'], 180))
        assert len(set(warm['pids'])) == 4
        write(folder/'worker-warmup.json', warm)
    if spec['processing'] == 'active':
        command(['docker', 'start']+(['bench-dispatcher', 'bench-search'] if 'architecture' in spec else WORKERS))
        if 'architecture' not in spec: wait_workers()
    else: stop_workers()
    write(folder/'spec.json', spec)
    write(folder/'before.json', {'containers': container_state(), 'state': inspect_state()})
    command(['aws', 's3', 'cp', str(folder/'spec.json'), f's3://{bucket}/control/request.json', '--only-show-errors'])
    deadline = time.monotonic()+240+spec['baseline_seconds']+spec['duration_seconds']+spec['recovery_seconds']+spec['upload_timeout_seconds']+90
    completed = False
    with (folder/'server-samples.jsonl').open('w') as stream:
        while time.monotonic() < deadline:
            observation = {'epoch': time.time(), 'containers': container_state(), 'pipeline': summary(seeds)}
            if 'architecture' in spec:
                from architecture_processes import live_children
                try:
                    observation['worker_children'] = live_children(observation['containers']['bench-search']['host_pid'])
                except (OSError, StopIteration, ValueError):
                    observation['worker_children'] = []  # A missing/replaced child invalidates the trial.
            stream.write(json.dumps(observation)+'\n'); stream.flush()
            result = subprocess.run(['aws', 's3', 'cp', f's3://{bucket}/control/completed/{spec["name"]}.json', '-', '--only-show-errors'], capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                completed = json.loads(result.stdout)['finished']; break
            time.sleep(10)
    command(['aws', 's3', 'sync', f's3://{bucket}/client-evidence/{spec["name"]}/', str(folder/'client'), '--only-show-errors'])
    write(folder/'after-observation.json', {'containers': container_state(), 'state': inspect_state(), 'summary': summary(seeds)})
    archive(folder)
    assert completed, 'Client did not complete; evidence retained without deletion'
    rows = [json.loads(line) for line in (folder/'client/uploads.jsonl').read_text().splitlines()] if (folder/'client/uploads.jsonl').exists() else []
    sentinel_rows = [json.loads(line) for line in (folder/'client/sentinel.jsonl').read_text().splitlines()] if (folder/'client/sentinel.jsonl').exists() else []
    if 'architecture' in spec:
        rows += sentinel_rows
    accepted = [row['video_id'] for row in rows if row['outcome'] == 'accepted']
    assert len(accepted) == len(set(accepted)), 'Duplicate acceptance identity'
    # Include late commits in reconciliation; never silently drop failed browser attempts.
    logical = [v['video_id'] for v in inspect_state()['videos'] if v['video_id'] not in seeds]
    write(folder/'reconciliation.json', {'accepted_ids': accepted, 'logical_ids': logical,
          'unreturned_commits': sorted(set(logical)-set(accepted)), 'missing_commits': sorted(set(accepted)-set(logical))})
    assert set(accepted) <= set(logical), 'Accepted video missing'
    stop_workers()
    write(folder/'before-teardown.json', inspect_state())
    with (folder/'before-teardown.sql').open('wb') as output:
        subprocess.run(['docker', 'exec', 'bench-postgres', 'pg_dump', '-U', 'bench', '-d', 'vindex_pipeline_bench', '--no-owner', '--no-acl'], stdout=output, stderr=subprocess.DEVNULL, timeout=60, check=True)
    if logical:
        fixture = ROOT/'fixtures'/Path(spec['file']).name
        if 'architecture' in spec:
            assert set(accepted) == set(logical), 'Unreturned commits preserved for investigation'
            sentinel_ids = {r['video_id'] for r in sentinel_rows if r['outcome'] == 'accepted'}
            inputs = []
            for vid in logical:
                path = ROOT/'fixtures'/Path(spec['sentinel_file']).name if vid in sentinel_ids else fixture
                inputs.append({'video_id': vid, 'kind': 'sentinel' if vid in sentinel_ids else 'background',
                               'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
            write(folder/'verification-input.json', {'seeds': seeds, 'expected': inputs, 'architecture': spec['architecture']})
            verification = json.loads(command(['docker', 'exec', 'bench-api', 'python', '/bench/architecture_state.py',
                '/bench/'+str((folder/'verification-input.json').relative_to(ROOT))], 600))
            for vid in sentinel_ids:
                write(folder/(vid+'-metrics.json'), app_request('/videos/'+vid+'/metrics'))
        else:
            verification = state('verify', seeds, logical, {'bytes': fixture.stat().st_size, 'sha256': hashlib.sha256(fixture.read_bytes()).hexdigest()})
        write(folder/'verification.json', verification); archive(folder)
        retirement = state('retire', seeds, logical, stopped=True)
        write(folder/'retirement.json', retirement)
        deleted = [app_request('/videos/'+vid, 'DELETE', 60) for vid in logical]
        write(folder/'deletions.json', deleted)
        write(folder/'cleanup.json', state('residual', seeds, logical))
    # Stopped workers can leave queued messages for retired test jobs. This Redis
    # database is private to this experiment; clear only after all IDs are reconciled.
    command(['docker', 'exec', 'bench-redis', 'redis-cli', 'FLUSHDB'])
    assert state('summary', seeds)['videos'] == 0 and not any(queues_empty().values())
    archive(folder)
    return {'trial': spec['name'], 'accepted': len(accepted), 'logical': len(logical), 'at': utc()}


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--seed', action='store_true'); parser.add_argument('--plan', type=Path)
    args = parser.parse_args()
    if args.seed: seed(); return
    plan = json.loads(args.plan.read_text()); settings = json.loads((ROOT/'run-settings.json').read_text())
    destination = ROOT/'results'/plan['name']; destination.mkdir()
    write(destination/'plan.json', plan); archive(destination/'plan.json')
    seeds = json.loads((ROOT/'results/seeds.json').read_text())['ids']
    if 'protected_seed_ids' in plan:
        assert seeds == plan['protected_seed_ids'], 'Protected corpus changed after plan registration'
    progress = {'started_at': utc(), 'finished': False, 'completed': []}
    began = time.monotonic()
    try:
        for spec in plan['trials']:
            if time.monotonic()-began+900 > plan['max_wall_seconds']: raise RuntimeError('Campaign time budget exhausted')
            spec = {**spec, 'seeds': seeds, 'commit': settings['commit'], 'environment': 'staging', 'origin': 'https://bench-proxy:8443'}
            if 'upload_client_vcpu' in plan:
                spec.update(upload_client_vcpu=plan['upload_client_vcpu'], query_client_vcpu=plan['query_client_vcpu'])
            progress['active_trial'] = spec['name']; write(destination/'progress.json', progress); archive(destination/'progress.json')
            progress['completed'].append(trial(destination/spec['name'], spec, settings))
            write(destination/'progress.json', progress); archive(destination/'progress.json')
            print(json.dumps(progress['completed'][-1]), flush=True)
        progress['finished'] = True
    except Exception as exc:
        progress['failure_type'] = type(exc).__name__
        progress['failure'] = str(exc) if isinstance(exc, (AssertionError, RuntimeError)) else 'Inspect retained evidence'
        raise
    finally:
        progress['finished_at'] = utc(); write(destination/'progress.json', progress)
        snapshot(plan['name']+'-final'); archive(); seal_archive(plan['name'])


if __name__ == '__main__': main()
