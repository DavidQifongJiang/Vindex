"""Analyze complete, paired pipeline waves without accessing application services."""
import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path

if __package__:
    from .analyze_upload_comparison import paired_bootstrap, wilson
else:
    from analyze_upload_comparison import paired_bootstrap, wilson


def read(path): return json.loads(path.read_text(encoding='utf-8'))


def analyze_campaign(root, *, iterations=10000):
    root=Path(root)
    plan,progress=read(root/'plan.json'),read(root/'progress.json')
    if not progress.get('finished'): raise ValueError('Incomplete campaign cannot pass comparison gates')
    expected_names=[w['name'] for w in plan['waves']]
    if len(set(expected_names)) != len(expected_names) or [w['wave'] for w in progress['completed']] != expected_names:
        raise ValueError('Campaign wave accounting mismatch')
    groups=defaultdict(dict); all_ids=set(); warmups=0
    for spec in plan['waves']:
        folder=root/spec['name']
        if not folder.resolve().is_relative_to(root.resolve()): raise ValueError('Unsafe wave path')
        if read(folder/'spec.json')!=spec: raise ValueError('Wave spec differs from locked plan')
        run=read(folder/'browser/run.json'); completion=read(folder/'browser/completion.json')
        rows=[json.loads(line) for line in (folder/'browser/attempts.jsonl').read_text().splitlines() if line]
        verified=read(folder/'verification.json')['verified']; cleanup=read(folder/'cleanup.json')
        fixture=read(folder/'browser/fixtures.jsonl')
        if not completion.get('finished_all_planned_waves') or not completion.get('all_attempts_ready') or len(rows)!=spec['concurrency'] or completion['planned_attempts']!=len(rows):
            raise ValueError('Missing or extra attempts')
        if any(r['outcome']!='accepted' or r.get('readiness_outcome')!='ready' for r in rows):
            raise ValueError('Failed attempts retained; full paired latency matrix unavailable')
        if sorted(r['client'] for r in rows)!=list(range(spec['concurrency'])): raise ValueError('Duplicate client identity')
        ids={r['video_id'] for r in rows}
        if len(ids)!=len(rows) or ids & all_ids: raise ValueError('Duplicate logical video across waves')
        all_ids.update(ids)
        if len(verified)!=len(rows) or {v['video_id'] for v in verified}!=ids or any(v['sha256']!=fixture['sha256'] or v['vectors']<1 for v in verified):
            raise ValueError('Verification does not match measured attempts')
        if any(v['bytes']!=next(r['bytes'] for r in rows if r['video_id']==v['video_id']) for v in verified):
            raise ValueError('Stored input size differs from measured fixture')
        if cleanup['remaining_objects'] or cleanup['remaining_vectors'] or {v['video_id'] for v in cleanup['deleted']}!=ids:
            raise ValueError('Incomplete cleanup')
        if spec.get('role')=='warmup': warmups+=len(rows); continue
        if any(r['mode']!=spec['mode'] or r['worker_state']!=spec['worker_state'] for r in rows): raise ValueError('Wrong upload mode or worker state')
        identity=(spec['worker_state'],spec['fixture'],spec['concurrency'],json.dumps(run['network'],sort_keys=True),run['commit'])
        slot=(spec['pair'],spec['mode'])
        if slot in groups[identity]: raise ValueError('Duplicate paired wave')
        groups[identity][slot]={'rows':sorted(rows,key=lambda r:r['client']),'sha256':fixture['sha256'],
                                'verified':verified,'run':run}
    cells=[]
    for identity,waves in groups.items():
        pairs=sorted({pair for pair,mode in waves})
        if set(waves)!={(p,m) for p in pairs for m in ['api','direct']}: raise ValueError('Unpaired comparison')
        if len({v['sha256'] for v in waves.values()})!=1: raise ValueError('Input bytes differ across paired trials')
        settings={json.dumps({k:v['run']['readiness'][k] for k in ['poll_interval_ms','probe_timeout_ms','deadline_seconds','search']},sort_keys=True) for v in waves.values()}
        if len(settings)!=1: raise ValueError('Probe contracts differ across modes')
        n=len(pairs)*identity[2]
        result={'worker_state':identity[0],'fixture':identity[1],'concurrency':identity[2],'network':json.loads(identity[3]),
                'commit':identity[4],'paired_waves':len(pairs),'attempts_per_mode':n,'verified_per_mode':n,
                'observed_full_readiness_rate':1.0,'descriptive_wilson_95pct':wilson(n,n),
                'metrics':{},'gates':{'integrity_and_cleanup':'passed','observed_readiness_99_percent':'passed',
                'population_reliability':'unverified: repeated fixtures and concurrent clients are not independent population trials',
                'absolute_staging_slo':'unverified: no absolute product SLO set'}}
        selectors={'accepted':lambda r:r['upload_seconds'],
                   'searchable':lambda r:r['searchable']['observed_seconds'],
                   'playable':lambda r:r['playable']['observed_seconds'],
                   'search_after_acceptance':lambda r:r['searchable']['after_acceptance_seconds'],
                   'playback_after_acceptance':lambda r:r['playable']['after_acceptance_seconds']}
        for metric,get in selectors.items():
            matrices={m:[[get(r) for r in waves[(p,m)]['rows']] for p in pairs] for m in ['api','direct']}
            if any(v<=0 for matrix in matrices.values() for row in matrix for v in row): raise ValueError('Invalid milestone duration')
            result['metrics'][metric]=paired_bootstrap(matrices['api'],matrices['direct'],iterations=iterations,seed=20261110)
            if metric in ['searchable','playable']:
                lo,hi=result['metrics'][metric]['p95']['ratio_95pct_paired_wave_bootstrap']
                result['gates'][metric+'_p95_non_regression']=('passed' if hi<=1.10 else 'failed' if lo>1.10 else 'inconclusive') if n>=100 else 'unverified: exploratory pilot sample'
        result['processing_retries']=sum(not v['single_attempt'] for wave in waves.values() for v in wave['verified'])
        cells.append(result)
    return {'generated_at':datetime.now(timezone.utc).isoformat(),'campaign':plan['name'],
            'method':f'{iterations} paired-wave bootstrap resamples, seed 20261110, nearest-rank quantiles; clients stay clustered',
            'unique_video_ids_including_warmups':len(all_ids),'excluded_warmups':warmups,'cells':cells}


def main():
    p=argparse.ArgumentParser();p.add_argument('--campaign',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    report=analyze_campaign(a.campaign)
    a.output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'cells':len(report['cells']),'report':str(a.output)}))


if __name__=='__main__': main()
