"""Same-scope semantic-before versus ES-hybrid-after through the real API route."""
from collections import Counter
import csv
from datetime import datetime,timezone
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import random
from time import perf_counter
from unittest.mock import patch
from uuid import uuid4,uuid5,NAMESPACE_URL

import numpy as np
from fastapi import FastAPI
from fastapi.testclient import TestClient
from qdrant_client import QdrantClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema,DropSchema
from sentence_transformers import SentenceTransformer

from app.api.search import router
from app.db.models import Base,Video,SearchProjection
from app.db import session as database,search_repository as repo
from app.services import search_service,search_observability as telemetry
from app.services.auth_service import AuthUser,get_current_user
from app.services.search_contracts import TranscriptSnapshot
from app.services.projection_indexes import ElasticsearchProjectionIndex,QdrantProjectionIndex
from benchmarks.smoke_library_sample import passages,REVISION

ROOT = Path(__file__).resolve().parent
OUT = ROOT/'library_queries_500'


def main():
    plan_path=OUT/'before_after_api500_plan.json'
    plan=json.loads(plan_path.read_text())
    query_path=OUT/'queries.csv'
    if sha256(query_path.read_bytes()).hexdigest()!=plan['query_sha256']:
        raise ValueError('Query set changed')
    queries=list(csv.DictReader(query_path.open(encoding='utf-8')))
    if len(queries)!=500:raise ValueError('Expected500 queries')
    source=json.loads((ROOT/'lecture_captions/manifest.json').read_text())['captions']
    schema='vindex_eval_'+uuid4().hex
    target='vindex-before-after-'+uuid4().hex
    root=create_engine(os.environ['VINDEX_TEST_DATABASE_URL'])
    with root.begin() as conn:conn.execute(CreateSchema(schema))
    engine=create_engine(os.environ['VINDEX_TEST_DATABASE_URL'],connect_args={'options':f'-csearch_path={schema}'})
    environment={'LIBRARY_SEARCH_ENABLED':'true','SEARCH_PROJECTIONS_ENABLED':'true',
        'EMBEDDING_MODEL':'all-MiniLM-L6-v2','SEARCH_EMBEDDING_REVISION':REVISION,
        'SEARCH_LEXICAL_TARGET':target,'SEARCH_SEMANTIC_TARGET':target,'SEARCH_VECTOR_SIZE':'384',
        'ELASTICSEARCH_URL':os.environ['VINDEX_TEST_ELASTICSEARCH_URL'],'QDRANT_URL':os.environ['VINDEX_TEST_QDRANT_URL']}
    report={'generated_at':datetime.now(timezone.utc).isoformat(),'plan':plan,'rows':[],
            'measurement':'FastAPI ASGI route, real PostgreSQL publication/scope/revalidation and real Elasticsearch/Qdrant. Fixed test auth owner; excludes JWT, ingress/TLS, video ingestion. Caption passages accepted into isolated database.',
            'baseline':'semantic-only ablation on identical library scope, not the historical per-video endpoint',
            'after':'unchanged hybrid mode with Elasticsearch and Qdrant, depth50 and equal RRF k60',
            'temporary_schema':schema,'temporary_target':target,'videos':50,'passages':0}
    handler=logging.FileHandler(OUT/'before_after_api500.traces.jsonl',encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(message)s'))
    previous=telemetry.logger.handlers[:];telemetry.logger.handlers=[handler]
    elastic=qdrant=None
    video_keys={}
    try:
        with patch.dict(os.environ,environment),patch.object(database,'engine',engine),patch.object(database,'SessionLocal',sessionmaker(engine,autoflush=False)):
            Base.metadata.create_all(engine)
            start=perf_counter()
            model=SentenceTransformer('all-MiniLM-L6-v2',revision=REVISION,local_files_only=True,device='cpu')
            if model[0].auto_model.config._commit_hash!=REVISION:raise ValueError('Model mismatch')
            search_service._embedding_model=model
            report['model_load_seconds']=perf_counter()-start
            elastic=ElasticsearchProjectionIndex(target)
            qdrant=QdrantClient(url=environment['QDRANT_URL'],check_compatibility=False)
            vector_index=QdrantProjectionIndex(target,384,qdrant)
            report['versions']={'elasticsearch':elastic.request('GET','')['version']['number'],'qdrant':qdrant.info().version}
            start=perf_counter()
            for entry in source:
                key=entry['video_key'];path=ROOT.parent/entry['file_path']
                if sha256(path.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('Caption changed')
                segments=passages(path);raw=json.dumps(segments).encode()
                video_id=str(uuid5(NAMESPACE_URL,key));video_keys[video_id]=key
                with database.create_session() as db:
                    db.add(Video(video_id=video_id,title=key,owner_user_id='before-after-owner',status='processed',segments_status='completed',segments_path=key+'.json'))
                    db.commit()
                    revision=repo.register_revision(db,video_id,key+'.json',raw);revision_id=revision.revision_id;db.commit()
                    jobs=[(j.job_id,j.generation_id) for j in db.query(SearchProjection).filter_by(video_id=video_id).all()]
                snapshot=TranscriptSnapshot.from_manifest(video_id,revision_id,raw)
                vectors=model.encode([p.text for p in snapshot.passages],normalize_embeddings=True,batch_size=32).tolist()
                for job,generation in jobs:
                    with database.create_session() as db:attempt=repo.claim(db,job)
                    index,embeddings=(elastic,None) if generation=='lexical-v1' else (vector_index,vectors)
                    index.write(snapshot,attempt,embeddings)
                    if not index.verify(snapshot,attempt,embeddings):raise ValueError('Index verification failed')
                    with database.create_session() as db:repo.publish(db,job,attempt)
                report['passages']+=len(snapshot.passages)
                print('Indexed',key,flush=True)
            report['setup_seconds']=perf_counter()-start
            app=FastAPI();app.include_router(router)
            app.dependency_overrides[get_current_user]=lambda:AuthUser('before-after-owner')
            with TestClient(app) as client:
                def request(mode,q,round_index):
                    start=perf_counter();response=client.post('/search',json={'query':q['query'],'mode':mode,'limit':5});elapsed=(perf_counter()-start)*1000
                    payload=response.json();hits=payload.get('results',[])
                    if any(h['video_id'] not in video_keys for h in hits):raise ValueError('Out-of-scope result')
                    return dict(query_id=q['query_id'],query=q['query'],query_type=q['query_type'],split=q['split'],mode=mode,round=round_index,
                        latency_ms=elapsed,http_status=response.status_code,degraded=payload.get('degraded',False),coverage=payload.get('coverage'),
                        branches=payload.get('branches'),request_id=payload.get('request_id'),results=[dict(h,video_key=video_keys[h['video_id']]) for h in hits])
                report['warmups']=[request(m,queries[0],-1) for m in plan['modes']]
                rng=random.Random(plan['seed'])
                for round_index in range(plan['rounds']):
                    order=list(queries);rng.shuffle(order)
                    for i,q in enumerate(order):
                        modes=list(plan['modes']);rng.shuffle(modes)
                        for mode in modes:report['rows'].append(request(mode,q,round_index))
                        if (i+1)%25==0:print('Round',round_index+1,'queries',i+1,'/500',flush=True)
                    (OUT/'before_after_api500_results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
            report['summary']={}
            for m in plan['modes']:
                rows=[r for r in report['rows'] if r['mode']==m]
                report['summary'][m]=dict(requests=len(rows),http_statuses=dict(Counter(str(r['http_status']) for r in rows)),
                    degraded=sum(r['degraded'] for r in rows),p50_ms=float(np.percentile([r['latency_ms'] for r in rows],50)),p95_ms=float(np.percentile([r['latency_ms'] for r in rows],95)))
            (OUT/'before_after_api500.metrics.txt').write_text(telemetry.render_metrics(),encoding='utf-8')
    finally:
        cleanup={}
        for name,operation in [('elasticsearch',lambda:elastic.request('DELETE',target) if elastic else None),('qdrant',lambda:qdrant.delete_collection(target) if qdrant and qdrant.collection_exists(target) else None)]:
            try:operation();cleanup[name]='deleted'
            except Exception as exc:cleanup[name]=str(exc)
        if elastic:elastic.close()
        if qdrant:qdrant.close()
        engine.dispose()
        try:
            with root.begin() as conn:conn.execute(DropSchema(schema,cascade=True))
            cleanup['postgres_schema']='deleted'
        except Exception as exc:cleanup['postgres_schema']=str(exc)
        root.dispose();telemetry.logger.handlers=previous;handler.close()
        report['cleanup']=cleanup
        (OUT/'before_after_api500_results.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report.get('summary',{}),indent=2))


if __name__=='__main__':main()
