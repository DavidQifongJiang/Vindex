"""Apply the frozen 120s packaging policy to exposed full500 saved results."""
import json
from collections import defaultdict
from hashlib import sha256
from pathlib import Path
from benchmarks.context_containment_experiment import expand
from benchmarks.candidate_diagnostics import content_key
from benchmarks.score_accepted_candidates import accepted_labels


def main():
    source=Path('benchmarks/library_queries_500/before_after_api500_results.json')
    corpus_path=Path('tmp/search-quality-v2/run-01/corpus.json')
    output=Path('tmp/search-quality-v2/context-full500-01')
    output.mkdir(exist_ok=False)
    (output/'plan.json').write_text(json.dumps(dict(window_seconds=120,source_sha256=sha256(source.read_bytes()).hexdigest(),
        corpus_sha256=sha256(corpus_path.read_bytes()).hexdigest(),
        interpretation='Exposed full500 regression replay, not fresh confirmation. Exact child containment, not rejudged Hit@5.',
        identity='Remap run-specific IDs only after exact video, ordinal, start, end and text match.'),indent=2))
    corpus=json.loads(corpus_path.read_text())
    groups=defaultdict(list)
    identity=lambda p:(p['video_id'],p['ordinal'],p['start'],p['end'],p['text'])
    mapping={identity(p):p for p in corpus}
    for p in corpus:groups[p['video_id'],p['revision_id']].append(p)
    for siblings in groups.values():siblings.sort(key=lambda p:p['ordinal'])
    labels=accepted_labels()
    rows=[]
    known_by_query={}
    for r in json.loads(source.read_text())['rows']:
        if r['round']:continue
        hits=[mapping[identity(dict(p,start=p['start_seconds'],end=p['end_seconds']))] for p in r['results']]
        if r['query_id'] not in known_by_query:
            known_by_query[r['query_id']]={p['passage_id'] for p in corpus if labels.get(content_key(r['query'],p),{}).get('rating')==2}
        known=known_by_query[r['query_id']]
        windows=[expand(p,groups[p['video_id'],p['revision_id']],120) for p in hits]
        contained=[sorted(known.intersection(p['child_ids'])) for p in windows]
        before=any(p['passage_id'] in known for p in hits)
        rows.append(dict(query_id=r['query_id'],query=r['query'],query_type=r['query_type'],split=r['split'],method=r['mode'],
                         before=before,after=any(contained),known_answer_children=contained,results=windows,
                         original_results=hits))
    assert len(rows)==1500 and len({(r['query_id'],r['method']) for r in rows})==1500
    summary=[]
    for split in ['development','reserved_evaluation','all']:
        for method in ['semantic','lexical','hybrid']:
            selected=[r for r in rows if r['method']==method and r['query_type']!='no_answer_candidate' and (split=='all' or r['split']==split)]
            summary.append(dict(split=split,method=method,queries=len(selected),before=sum(r['before'] for r in selected),
                after=sum(r['after'] for r in selected),gained=[r['query_id'] for r in selected if r['after'] and not r['before']],
                lost=[r['query_id'] for r in selected if r['before'] and not r['after']]))
    (output/'results.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
    (output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
