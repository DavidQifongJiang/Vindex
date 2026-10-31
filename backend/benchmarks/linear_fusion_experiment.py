"""One fixed score-aware fusion hypothesis, without parameter selection."""
import json
import math
from hashlib import sha256
from pathlib import Path
from app.services.hybrid_search import reciprocal_rank_fusion
from benchmarks.score_accepted_candidates import accepted_labels, evaluate


def fuse(branches):
    union = {}
    for branch, hits in branches.items():
        if not hits:
            continue
        values = [h['score'] for h in hits]
        if not all(math.isfinite(v) for v in values):
            raise ValueError('Nonfinite input score')
        low, high = min(values), max(values)
        for h in hits:
            normalized = (h['score'] - low) / (high - low) if high > low else 1.0
            item = union.setdefault(h['passage_id'], dict(h, score=0.0, contributions={}))
            item['contributions'][branch] = normalized * .5
            item['score'] += normalized * .5
    return sorted(union.values(),key=lambda p:(-p['score'],p['passage_id']))


def main():
    source = Path('tmp/search-quality-v2/run-01/results.json')
    output = Path('tmp/search-quality-v2/linear-fusion-01')
    output.mkdir(exist_ok=False)
    plan = dict(source_sha256=sha256(source.read_bytes()).hexdigest(),candidate_depth=50,
                normalization='per query and branch minmax; flat nonempty branch contributes 1; missing contributes 0',
                weights=dict(semantic=.5,lexical=.5),hypothesis='Score gaps convey distinctions discarded by rank-only fusion.',
                selection='One fixed equal-weight candidate, no grid or query-specific tuning.',
                reference='https://www.elastic.co/docs/reference/elasticsearch/rest-apis/retrievers/linear-retriever')
    (output/'plan.json').write_text(json.dumps(plan,indent=2))
    rows=[]
    for row in json.loads(source.read_text())['rows']:
        if row['round'] or row['profile'] != 'baseline':continue
        b=row['candidates']['rankings']
        for method,hits in [('rrf60',reciprocal_rank_fusion(b['semantic'],b['lexical'],limit=5)),('linear_minmax_equal',fuse(b)[:5])]:
            rows.append(dict(query_id=row['query_id'],query=row['query'],query_type=row['query_type'],method=method,results=hits))
    summary,scores,pending=evaluate(rows,accepted_labels())
    for name,data in [('rankings',rows),('summary',summary),('scores',scores),('pending',pending)]:
        (output/(name+'.json')).write_text(json.dumps(data,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
