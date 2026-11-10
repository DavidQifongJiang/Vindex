"""Audit recorded runtime conditions separately from latency statistics."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path


LIMITS = {'bench-api':(.5,2048), 'bench-proxy':(.5,128), 'bench-postgres':(.5,768),
          'bench-redis':(.25,256), 'bench-qdrant':(.5,1024), 'bench-dispatcher':(.25,512),
          'bench-search':(2,4096), 'bench-playback':(1,2048), 'bench-browser':(2,4096)}
WORKERS = ['bench-search','bench-playback']


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def audit_runtime(before, after):
    if before['state']['videos'] or before['state']['active'] or any(before['queues'].values()):
        raise ValueError('Wave did not start with empty state and queues')
    if set(before['containers'])!=set(LIMITS) or set(after)!=set(LIMITS):
        raise ValueError('Unexpected runtime components')
    usage={}
    for name,(cpus,mib) in LIMITS.items():
        a,b=before['containers'][name],after[name]
        if not all(v['running'] and not v['oom_killed'] for v in [a,b]):
            raise ValueError('Stopped or OOM-killed component')
        if any(a[k]!=b[k] for k in ['id','started_at','image_id','cpu_nano','memory_bytes']):
            raise ValueError('Component changed during a measured wave')
        if a['cpu_nano']!=int(cpus*1e9) or a['memory_bytes']!=mib*1024**2:
            raise ValueError('Runtime capacity differs from preregistration')
        seconds=(int(b['cpu']['usage_usec'])-int(a['cpu']['usage_usec']))/1e6
        if seconds<0: raise ValueError('CPU accounting reset')
        usage[name]=seconds
    return usage


def audit_campaign(root, locked_plan=None):
    root=Path(root); plan=read(root/'plan.json')
    if not read(root/'progress.json').get('finished'): raise ValueError('Incomplete campaign')
    # The immutable preregistration is checked structurally because the on-host
    # JSON writer changes whitespace; input bytes and the canonical plan are exact.
    if locked_plan and plan!=read(Path(locked_plan)): raise ValueError('Plan differs from preregistration')
    last_workers=None; warmups=set(); images={}; resources=defaultdict(list)
    for spec in plan['waves']:
        folder=root/spec['name']
        if not folder.resolve().is_relative_to(root.resolve()): raise ValueError('Unsafe wave path')
        before=read(folder/'before.json'); after=read(folder/'after-containers.json')
        usage=audit_runtime(before,after)
        for name,record in before['containers'].items():
            if name in images and images[name]!=record['image_id']: raise ValueError('Image changed across campaign')
            images[name]=record['image_id']
        workers=tuple((before['containers'][n]['id'],before['containers'][n]['started_at']) for n in WORKERS)
        cohort=(spec['worker_state'],spec['fixture'],spec['concurrency'])
        if spec['worker_state']=='cold':
            if last_workers and any(a==b for a,b in zip(last_workers,workers)):
                raise ValueError('A cold worker was reused')
        elif last_workers and workers!=last_workers:
            raise ValueError('Warm workers restarted between waves')
        if spec.get('role')=='warmup': warmups.add(cohort)
        if locked_plan and spec['worker_state']=='warm' and cohort not in warmups:
            raise ValueError('Warm cohort lacks its excluded warm-up')
        last_workers=workers
        run=read(folder/'browser/run.json')
        if hashlib.sha256((folder/'preflight.json').read_bytes()).hexdigest()!=run['readiness']['preflight_sha256']:
            raise ValueError('Preflight evidence hash mismatch')
        fixture=read(folder/'browser/fixtures.jsonl')
        if locked_plan:
            expected=plan['fixtures'][spec['fixture']]
            if (run['commit']!=plan['commit'] or fixture['file']!=spec['fixture'] or fixture['sha256']!=expected['sha256']
                    or run['files']!=[{'name':spec['fixture'],'bytes':expected['bytes']}]):
                raise ValueError('Measured source or input differs from locked plan')
        rows=[json.loads(line) for line in (folder/'browser/attempts.jsonl').read_text().splitlines()]
        for row in rows:
            if row['observed_mode']!=spec['mode']: raise ValueError('Observed route differs from declared mode')
            if locked_plan and row['bytes']!=expected['bytes']: raise ValueError('Attempt input size differs from locked plan')
            for branch in ['searchable','playable']:
                observed=row[branch]['observed_seconds']
                if abs(observed-row['upload_seconds']-row[branch]['after_acceptance_seconds'])>1e-6:
                    raise ValueError('Milestones use inconsistent time origins')
                if observed>run['readiness']['deadline_seconds']: raise ValueError('Readiness arrived after deadline')
        if spec.get('role')!='warmup':
            resources[(*cohort,spec['mode'])].append(usage)
    return {'campaign':plan['name'],'runtime_audit':'passed','waves':len(plan['waves']),
            'locked_plan_verified':bool(locked_plan),'image_ids':images,
            'cpu_accounting_note':'Container CPU deltas include browser setup and readiness polling; these are not upload-only CPU measurements.',
            'cleanup_note':'Per-wave remaining_objects checks the raw_videos prefix. Full-bucket residual reconciliation is a separate final teardown check.',
            'cpu_totals':[{'worker_state':k[0],'fixture':k[1],'concurrency':k[2],'mode':k[3],
                           'seconds':{name:sum(w[name] for w in waves) for name in LIMITS}} for k,waves in resources.items()]}


def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True)
    p.add_argument('--locked-plan',type=Path);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=audit_campaign(a.campaign,a.locked_plan)
    a.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'runtime_audit':report['runtime_audit'],'waves':report['waves']}))


if __name__=='__main__': main()
