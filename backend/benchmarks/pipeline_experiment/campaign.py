"""Runs on the dedicated host; no local desktop is needed after dispatch."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import ssl
import subprocess
import tarfile
import time
from urllib.request import Request, urlopen

ROOT = Path('/opt/vindex-experiment')
CONTAINERS = ['bench-api','bench-proxy','bench-postgres','bench-redis','bench-qdrant','bench-dispatcher','bench-search','bench-playback','bench-browser']


def command(args, timeout=120):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        # Raw errors may include private headers. Keep only the executable and exit code.
        raise RuntimeError(f'{args[0]} exited {result.returncode}')
    return result.stdout


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding='utf-8')


def utc(): return datetime.now(timezone.utc).isoformat()


def app_request(path, method='GET', timeout=60):
    token = json.loads((ROOT/'private.json').read_text())['token']
    request = Request('https://127.0.0.1:8443'+path, method=method, headers={'Cookie':'vindex_bench='+token})
    with urlopen(request, context=ssl._create_unverified_context(), timeout=timeout) as response:
        return json.loads(response.read())


def inspect_state(action='snapshot', expected=None, fixture=None):
    args = ['docker','exec','bench-api','python','/bench/inspect_state.py',action]
    if expected is not None: args += ['--expected',json.dumps(expected)]
    if fixture: args += ['--sha256',fixture['sha256'],'--bytes',str(fixture['bytes'])]
    return json.loads(command(args, 180))


def container_state():
    report = {}
    for name in CONTAINERS:
        item = json.loads(command(['docker','inspect',name]))[0]
        report[name] = {'id':item['Id'],'started_at':item['State']['StartedAt'],'running':item['State']['Running'],
                        'oom_killed':item['State']['OOMKilled'],'image_id':item['Image'],
                        'cpu_nano':item['HostConfig']['NanoCpus'],'memory_bytes':item['HostConfig']['Memory'],
                        'host_pid':item['State']['Pid'],'image_reference':item['Config']['Image'],
                        'command':item['Config']['Cmd']}
        pid = item['State']['Pid']
        if pid:
            cgroup = Path(f'/proc/{pid}/cgroup').read_text().strip().split(':')[-1]
            report[name]['cpu'] = dict(line.split() for line in (Path('/sys/fs/cgroup')/cgroup.lstrip('/')/'cpu.stat').read_text().splitlines())
    return report


def queues_empty():
    sizes = {name:int(command(['docker','exec','bench-redis','redis-cli','llen',name]).strip()) for name in ['search','playback','celery']}
    sizes['unacked'] = int(command(['docker','exec','bench-redis','redis-cli','hlen','unacked']).strip())
    return sizes


def wait_workers():
    for name in ['bench-search','bench-playback']:
        started = json.loads(command(['docker','inspect',name]))[0]['State']['StartedAt']
        for _ in range(90):
            p = subprocess.run(['docker','logs','--since',started,name],capture_output=True,text=True)
            if '@experiment ready.' in p.stdout+p.stderr: break
            time.sleep(1)
        else: raise RuntimeError('Worker did not become ready')


def archive(path=None):
    bucket = json.loads((ROOT/'run-settings.json').read_text())['bucket']
    path=path or ROOT/'results'
    relative=path.relative_to(ROOT/'results').as_posix()
    suffix='' if relative=='.' else relative
    command(['aws','s3','sync' if path.is_dir() else 'cp',str(path),f's3://{bucket}/evidence/{suffix}',
             '--region','us-east-1','--only-show-errors'],180)


def seal_archive(label):
    bucket=json.loads((ROOT/'run-settings.json').read_text())['bucket']
    root=ROOT/'results'
    files={p.relative_to(root).as_posix():{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
           for p in root.rglob('*') if p.is_file() and not p.name.startswith('archive-manifest-')}
    manifest={'label':label,'bucket':bucket,'sealed_at':utc(),'files':files}
    write(root/f'archive-manifest-{label}.json',manifest)
    target=ROOT/(label+'-evidence.tar.gz')
    with tarfile.open(target,'w:gz') as tar:
        for name in files: tar.add(root/name,arcname=name)
        tar.add(root/f'archive-manifest-{label}.json',arcname=f'archive-manifest-{label}.json')
    key='evidence/'+label+'-evidence.tar.gz'
    command(['aws','s3','cp',str(target),f's3://{bucket}/{key}','--region','us-east-1','--only-show-errors'],180)
    marker={'key':key,'label':label,'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    write(ROOT/'latest-archive.json',marker)
    command(['aws','s3','cp',str(ROOT/'latest-archive.json'),f's3://{bucket}/evidence/latest-archive.json','--region','us-east-1','--only-show-errors'],60)


def snapshot(label):
    destination = ROOT/'results'/'snapshots'/label
    destination.mkdir(parents=True, exist_ok=True)
    try: write(destination/'state.json', inspect_state())
    except Exception: write(destination/'state-error.json', {'captured':False})
    with (destination/'database.sql').open('wb') as stream:
        subprocess.run(['docker','exec','bench-postgres','pg_dump','-U','bench','-d','vindex_pipeline_bench','--no-owner','--no-acl'],stdout=stream,stderr=subprocess.DEVNULL,timeout=60,check=True)
    for name in CONTAINERS:
        with (destination/(name+'.log')).open('wb') as stream:
            subprocess.run(['docker','logs','--tail','2000',name],stdout=stream,stderr=subprocess.STDOUT,timeout=20)
    write(destination/'containers.json',container_state())
    archive(destination)


def run_wave(folder, spec, settings):
    wait_workers()
    if spec['worker_state'] == 'cold':
        assert inspect_state()['active'] == 0 and not any(queues_empty().values())
        command(['docker','restart','bench-search','bench-playback'],180)
        wait_workers()
    time.sleep(spec.get('jitter_seconds',0))
    before = inspect_state()
    queues = queues_empty()
    assert not before['videos'] and before['active'] == 0 and not any(queues.values())
    identities = container_state()
    file = ROOT/'fixtures'/spec['fixture']
    fixture = {'bytes':file.stat().st_size, 'sha256':hashlib.sha256(file.read_bytes()).hexdigest()}
    target = 'https://bench-proxy:8443/bench-'+spec['mode']+'/'
    preflight = {'isolated':True,'queue_empty':True,'observed_at':utc(),'commit':settings['commit'],
        'target':target,'worker_state':spec['worker_state'],'tls_mode':'self_signed_isolated',
        'media_bucket':settings['bucket'],'database':'bench-postgres/vindex_pipeline_bench','redis':'bench-redis/0 isolated host',
        'qdrant':'bench-qdrant/vindex_segments isolated service','worker_identity':json.dumps(identities),
        'capacity':'API .5CPU/2GiB, search 2CPU/4GiB, playback 1CPU/2GiB, browser 2CPU/4GiB; host m7i.2xlarge',
        'model_versions':'Whisper tiny, all-MiniLM-L6-v2; baked image '+settings['image'],
        'cleanup_plan':'verify -> application delete -> bounded residual cleanup -> checkpoint and snapshot before shutdown'}
    folder.mkdir(parents=True)
    write(folder/'preflight.json',preflight)
    write(folder/'before.json',{'containers':identities,'queues':queues,'state':before})
    write(folder/'spec.json',spec)
    manifest = {'environment':'staging','commit':settings['commit'],
        'targets':{mode:'https://bench-proxy:8443/bench-'+mode+'/' for mode in ['api','direct']},
        'files':['/bench/fixtures/'+spec['fixture']],'storage_state':'/bench/session.json','repetitions':1,
        'concurrency':spec['concurrency'],'timeout_seconds':600,
        'network':{'name':'20mbps-40ms','latency_ms':40,'upload_mbps':20,'download_mbps':100},
        'pipeline':{'mode':spec['mode'],'worker_state':spec['worker_state'],'tls_mode':'self_signed_isolated',
            'deadline_seconds':600,'poll_interval_ms':1000,'probe_timeout_ms':30000,
            'preflight_file':'/bench/'+str((folder/'preflight.json').relative_to(ROOT)),
            'search':{'query':'vector search','expected_text':'vector search','start_seconds':0,'end_seconds':4,'tolerance_seconds':1}}}
    write(folder/'manifest.json',manifest)
    args = ['docker','exec','bench-browser','/bench/runtime/node_modules/node/bin/node','/bench/backend/benchmarks/run_upload_comparison.cjs',
            '--manifest','/bench/'+str((folder/'manifest.json').relative_to(ROOT)), '--output','/bench/'+str((folder/'browser').relative_to(ROOT))]
    with (folder/'console.log').open('w') as stream:
        result = subprocess.run(args,stdout=stream,stderr=subprocess.STDOUT,timeout=700)
    write(folder/'after-containers.json',container_state())
    archive(folder)  # Preserve browser failures before any cleanup.
    assert result.returncode == 0, 'Browser wave failed; no data deleted'
    rows = [json.loads(line) for line in (folder/'browser'/'attempts.jsonl').read_text().splitlines()]
    ids = [r['video_id'] for r in rows]
    assert len(ids) == spec['concurrency'] and all(r.get('readiness_outcome') == 'ready' for r in rows)
    for _ in range(15):
        state = inspect_state()
        if state['active'] == 0 and not any(queues_empty().values()): break
        time.sleep(1)
    verification = inspect_state('verify',ids,fixture)
    write(folder/'verification.json',verification)
    for vid in ids:
        write(folder/(vid+'-metrics.json'),app_request('/videos/'+vid+'/metrics'))
    archive(folder)  # Verification and artifact pointers survive deletion.
    deleted = [app_request('/videos/'+vid,'DELETE') for vid in ids]
    cleanup = inspect_state('cleanup-residuals',ids)
    write(folder/'cleanup.json',{'deleted':deleted,'remaining_objects':cleanup['remaining_objects'],'remaining_vectors':cleanup['remaining_vectors']})
    archive(folder)
    return {'wave':spec['name'],'accepted':len(rows),'verified':len(verification['verified']),'completed_at':utc()}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--plan',type=Path)
    parser.add_argument('--inventory',action='store_true')
    parser.add_argument('--snapshot')
    args=parser.parse_args()
    if args.snapshot: snapshot(args.snapshot); seal_archive(args.snapshot); return
    if args.inventory:
        write(ROOT/'results'/'host.json',{'containers':container_state(),'cpu':command(['lscpu']),'recorded_at':utc()})
        return
    plan=json.loads(args.plan.read_text()); settings=json.loads((ROOT/'run-settings.json').read_text())
    destination=ROOT/'results'/plan['name']; destination.mkdir()
    write(destination/'plan.json',plan)
    archive(destination/'plan.json')
    started=time.monotonic()
    progress={'started_at':utc(),'plan':plan['name'],'completed':[],'finished':False}
    write(destination/'progress.json',progress)
    try:
        for _ in range(60):
            try:
                if app_request('/health',timeout=5).get('status') == 'ok': break
            except Exception: pass
            time.sleep(2)
        assert app_request('/bench/pipeline/warm-query','POST',120)['query_model_warmed']
        for spec in plan['waves']:
            if time.monotonic()-started+700 > plan['max_wall_seconds']: raise RuntimeError('Insufficient remaining campaign budget')
            progress['active_wave']=spec['name']; write(destination/'progress.json',progress); archive(destination/'progress.json')
            progress['completed'].append(run_wave(destination/spec['name'],spec,settings))
            write(destination/'progress.json',progress)
            print(json.dumps(progress['completed'][-1]),flush=True)
        progress['finished']=True
    except Exception as exc:
        progress['failure_type']=type(exc).__name__
        # Our exception strings contain stage names only; child stderr stays private in artifacts.
        progress['failure']=str(exc) if isinstance(exc,(AssertionError,RuntimeError)) else 'Inspect retained stage evidence'
        raise
    finally:
        progress['finished_at']=utc(); progress['elapsed_seconds']=time.monotonic()-started
        write(destination/'progress.json',progress)
        snapshot(plan['name']+'-final')
        archive()
        seal_archive(plan['name'])


if __name__=='__main__': main()
