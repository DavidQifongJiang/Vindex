"""Local immutable-fixture fetch cost, explicitly not deployed API latency."""
from collections import defaultdict
from hashlib import sha256
import json
from pathlib import Path
import random
from time import perf_counter
import numpy as np
from benchmarks.context_containment_experiment import expand


def main():
    output=Path('tmp/search-quality-v2/context-fetch-01');output.mkdir(exist_ok=False)
    fixtures=output/'fixtures';fixtures.mkdir()
    corpus_path=Path('tmp/search-quality-v2/run-01/corpus.json')
    source=Path('tmp/search-quality-v2/context-full500-01/results.json')
    rows=[r for r in json.loads(source.read_text()) if r['method']=='hybrid']
    groups=defaultdict(list)
    for p in json.loads(corpus_path.read_text()):groups[p['video_id'],p['revision_id']].append(p)
    manifest={}
    for identity,passages in groups.items():
        passages.sort(key=lambda p:p['ordinal']);path=fixtures/(identity[0]+'.json')
        raw=json.dumps(passages).encode();path.write_bytes(raw);manifest[identity]=(path,sha256(raw).hexdigest())
    plan=dict(queries=500,repeats=3,cap_seconds=120,seed=20260919,
              source_sha256=sha256(source.read_bytes()).hexdigest(),corpus_sha256=sha256(corpus_path.read_bytes()).hexdigest(),
              modes=['fresh_file_read','revision_keyed_memory_cache'],
              limits='Local filesystem fixtures only. OS cache uncontrolled. No network, owner authorization, S3, ES, API or browser cost. Not cold-start or production latency.')
    (output/'plan.json').write_text(json.dumps(plan,indent=2))
    cache={};timings=[];costs=[]
    for mode in plan['modes']:
        for repeat in range(3):
            order=list(rows);random.Random(20260919+repeat).shuffle(order)
            for row in order:
                start=perf_counter();loaded={};windows=[]
                for anchor in row['original_results']:
                    identity=(anchor['video_id'],anchor['revision_id'])
                    if identity not in loaded:
                        if mode=='revision_keyed_memory_cache' and identity in cache:loaded[identity]=cache[identity]
                        else:
                            path,digest=manifest[identity];raw=path.read_bytes()
                            if sha256(raw).hexdigest()!=digest:raise ValueError('Fixture revision content changed')
                            siblings=json.loads(raw)
                            if any((p['video_id'],p['revision_id'])!=identity for p in siblings):raise ValueError('Mixed revision fixture')
                            loaded[identity]=siblings
                            if mode=='revision_keyed_memory_cache':cache[identity]=siblings
                    siblings=loaded[identity]
                    canonical=next(p for p in siblings if p['passage_id']==anchor['passage_id'])
                    if any(canonical[k]!=anchor[k] for k in ['video_id','revision_id','start','end','text']):raise ValueError('Anchor mismatch')
                    window=expand(canonical,siblings,120)
                    if canonical['text'] not in window['text'] or window['end']-window['start']>120:raise ValueError('Invalid context')
                    # Context is a separate object; never overwrite canonical child identity/text.
                    windows.append(dict(anchor_passage_id=canonical['passage_id'],start=window['start'],end=window['end'],text=window['text']))
                serialized=json.dumps(windows).encode()
                timings.append(dict(mode=mode,repeat=repeat,query_id=row['query_id'],milliseconds=(perf_counter()-start)*1000))
                if repeat==0 and mode=='fresh_file_read':costs.append(dict(query_id=row['query_id'],videos_fetched=len(loaded),extra_context_bytes=len(serialized)))
    summary=dict(measurement=plan['limits'],queries=500,checks='All3000 measured requests preserve canonical anchor identity and bound same-revision context.',
        modes={m:dict(requests=sum(r['mode']==m for r in timings),p50_ms=float(np.percentile([r['milliseconds'] for r in timings if r['mode']==m],50)),p95_ms=float(np.percentile([r['milliseconds'] for r in timings if r['mode']==m],95))) for m in plan['modes']},
        mean_extra_context_bytes=float(np.mean([r['extra_context_bytes'] for r in costs])),mean_videos_fetched=float(np.mean([r['videos_fetched'] for r in costs])))
    for name,data in [('summary',summary),('timings',timings),('costs',costs)]:
        (output/(name+'.json')).write_text(json.dumps(data,indent=2))
    print(json.dumps(summary,indent=2))


if __name__=='__main__':main()
