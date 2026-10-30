"""Frozen, offline context packaging test; containment is NOT a new judgment."""
import json
from collections import defaultdict
from hashlib import sha256
from pathlib import Path

from app.services.hybrid_search import reciprocal_rank_fusion
from benchmarks.candidate_diagnostics import content_key
from benchmarks.score_accepted_candidates import accepted_labels


def expand(anchor, siblings, max_seconds):
    """Keep the complete anchor, add closest adjacent children within the cap."""
    index = next(i for i, p in enumerate(siblings) if p['passage_id'] == anchor['passage_id'])
    left = right = index
    center = (anchor['start'] + anchor['end']) / 2
    while True:
        choices = []
        if left and siblings[right]['end'] - siblings[left - 1]['start'] <= max_seconds:
            choices.append((abs((siblings[left - 1]['start'] + siblings[left - 1]['end']) / 2 - center), 0))
        if right + 1 < len(siblings) and siblings[right + 1]['end'] - siblings[left]['start'] <= max_seconds:
            choices.append((abs((siblings[right + 1]['start'] + siblings[right + 1]['end']) / 2 - center), 1))
        if not choices:
            break
        if min(choices)[1] == 0:
            left -= 1
        else:
            right += 1
    children = siblings[left:right + 1]
    return dict(anchor, start=children[0]['start'], end=children[-1]['end'],
                text=' '.join(p['text'] for p in children), anchor_start=anchor['start'],
                child_ids=[p['passage_id'] for p in children])


def main():
    source = Path('tmp/search-quality-v2/run-01')
    output = Path('tmp/search-quality-v2/context-containment-01')
    output.mkdir(exist_ok=False)
    plan = dict(window_seconds=[60, 120], ranking='unchanged per engine and RRF k60',
                purpose='Isolate answer packaging from retrieval; not the parent reindex experiment.',
                selection='All 150 development queries, all three methods, no query-specific rules.',
                measure='Exact accepted grade2 child containment; NOT Hit@5 for changed text.',
                source_hashes={n:sha256((source/n).read_bytes()).hexdigest() for n in ['results.json','corpus.json']})
    (output/'plan.json').write_text(json.dumps(plan, indent=2))
    corpus = json.loads((source/'corpus.json').read_text())
    raw = json.loads((source/'results.json').read_text())
    groups = defaultdict(list)
    for p in corpus:
        groups[p['video_id'],p['revision_id']].append(p)
    for siblings in groups.values():
        siblings.sort(key=lambda p:p['ordinal'])
        if any(b['ordinal'] != a['ordinal'] + 1 for a,b in zip(siblings,siblings[1:])):
            raise ValueError('Non-contiguous source corpus')
    labels = accepted_labels()
    rows = []
    for row in raw['rows']:
        if row['round'] or row['profile'] != 'baseline':
            continue
        branches = row['candidates']['rankings']
        methods = dict(branches, hybrid=reciprocal_rank_fusion(branches['semantic'], branches['lexical'], limit=5))
        known = {p['passage_id'] for p in corpus if labels.get(content_key(row['query'],p),{}).get('rating') == 2}
        for method,hits in methods.items():
            for cap in [30] + plan['window_seconds']:
                windows = [dict(p, child_ids=[p['passage_id']],anchor_start=p['start']) if cap == 30 else
                           expand(p,groups[p['video_id'],p['revision_id']],cap) for p in hits[:5]]
                contained = [sorted(known.intersection(p['child_ids'])) for p in windows]
                rows.append(dict(query_id=row['query_id'],query=row['query'],query_type=row['query_type'],
                                 method=method,window_seconds=cap,known_answer_children=contained,
                                 contains_known_answer=any(contained),results=windows))
    summaries = []
    for method in methods:
        base = {r['query_id']:r for r in rows if r['method']==method and r['window_seconds']==30 and r['query_type']!='no_answer_candidate'}
        for cap in [30]+plan['window_seconds']:
            selected = [r for r in rows if r['method']==method and r['window_seconds']==cap and r['query_id'] in base]
            summaries.append(dict(method=method,window_seconds=cap,queries=len(selected),
                contains_known_answer=sum(r['contains_known_answer'] for r in selected),
                gained=[r['query_id'] for r in selected if r['contains_known_answer'] and not base[r['query_id']]['contains_known_answer']],
                lost=[r['query_id'] for r in selected if not r['contains_known_answer'] and base[r['query_id']]['contains_known_answer']],
                mean_words_per_result=sum(len(p['text'].split()) for r in selected for p in r['results'])/(5*len(selected))))
    (output/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summaries,indent=2))
    print(json.dumps(summaries,indent=2))


if __name__ == '__main__':
    main()
