"""Historical timestamp scoring, paired uncertainty, and an unjudged review pool."""
import csv
from hashlib import sha256
import json
from pathlib import Path
import random
import numpy as np
from benchmarks.compare_retrieval import relevance


def generate(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not data.get('complete') or len(data['rows']) != 1200:
        raise ValueError('Incomplete paired experiment')
    labels = {r['label_id']:r for r in csv.DictReader(open(data['plan']['labels'],encoding='utf-8-sig'))}
    corpus = json.loads(Path(data['plan']['corpus']).read_text(encoding='utf-8'))
    videos = {v['video_key']:v['video_id'] for v in corpus['videos']}
    primary = [r for r in data['rows'] if r['round'] == 0]
    lookup = {(r['label_id'],r['mode'],r['scope']):r for r in primary}
    pool, mapping = {}, []
    for row in primary:
        label = labels[row['label_id']]
        for rank,hit in enumerate(row['results'],1):
            known = hit['video_id']==videos[label['video_key']] and relevance(dict(hit,start=hit['start_seconds'],end=hit['end_seconds']),label)
            if known:
                continue
            content = dict(query=label['query'],video_id=hit['video_id'],start=hit['start_seconds'],end=hit['end_seconds'],text=hit['text'])
            key = sha256(json.dumps(content,sort_keys=True).encode()).hexdigest()
            pool[key] = dict(content,review_id=key,rating=None)
            mapping.append(dict(review_id=key,label_id=row['label_id'],mode=row['mode'],scope=row['scope'],rank=rank))
    review = list(pool.values())
    random.Random(20260922).shuffle(review)
    for i,r in enumerate(review,1):r['item']=i
    rng = np.random.default_rng(20260922)
    uncertainty = {}
    query_ids = sorted(labels)
    for mode in data['plan']['modes']:
        uncertainty[mode] = {}
        for metric in ['hit_at_1','hit_at_5','mrr']:
            delta = np.array([lookup[q,mode,'library'][metric]-lookup[q,mode,'within_video'][metric] for q in query_ids])
            samples = delta[rng.integers(0,100,size=(10000,100))].mean(axis=1)
            groups = {}
            for i,q in enumerate(query_ids):groups.setdefault(labels[q]['video_key'],[]).append(i)
            groups = list(groups.values())
            clustered = [float(delta[[i for g in rng.integers(0,len(groups),size=len(groups)) for i in groups[g]]].mean()) for _ in range(10000)]
            uncertainty[mode][metric] = dict(delta=float(delta.mean()),paired_query_95=np.quantile(samples,[.025,.975]).tolist(),lecture_cluster_95=np.quantile(clustered,[.025,.975]).tolist())
    for name,value in [('unjudged_review_pool',review),('review_mapping',mapping),('paired_uncertainty',uncertainty)]:
        path.with_name(name+'.json').write_text(json.dumps(value,indent=2),encoding='utf-8')
    lines = ['# Within-video versus library-wide search', '',
        '100 original queries, ten original lecture clips, three engines, two scopes, two rounds: 1,200 requests.',
        'Same passage boundaries, pinned embedding model and client reuse. No context expansion.', '',
        '**These are designated-answer timestamp metrics, not fully adjudicated library-wide relevance.**',
        'A valid answer elsewhere may be unjudged; the review pool preserves these candidates without labeling them incorrect.', '',
        '| Scope | Mode | Hit@1 | Hit@5 | MRR | p50 / p95 ms |', '|---|---|---:|---:|---:|---:|']
    for scope,methods in data['summary'].items():
        for mode,s in methods.items():
            lines.append(f"| {scope} | {mode} | {s['hit_at_1']:.1%} | {s['hit_at_5']:.1%} | {s['mrr']:.4f} | {s['p50_ms']:.1f} / {s['p95_ms']:.1f} |")
    unhealthy=sum(r['http_status']!=200 or r['degraded'] for r in data['rows'])
    lines += ['',f'Failed or degraded requests: {unhealthy}. Quality averages above use successful, nondegraded requests; inspect results.json counts.',
        f'Unjudged distinct query-passage pairs queued: {len(review)}. Method and rank are in a separate mapping.',
        'Scope-difference bootstrap intervals use first-round paired queries and also resample the ten lecture clusters.',
        'These intervals do not account for missing alternative-answer labels or reused-query selection bias.',
        'The within-video condition uses the current common API/retrieval pipeline with a video filter; it is not a rerun of the historical endpoint.',
        'Historical 65% / 90% / 0.7457 is a reference, not a guaranteed reproduction. Current pipeline differences can affect ties and results.',
        'Local ASGI timings exclude ingress, TLS, JWT and production concurrency.',
        'No new AI grading or deployment was performed. Final library-wide relevance comparison requires review of alternatives.']
    path.with_name('REPORT.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Completed. Report and unjudged review pool saved.',flush=True)
