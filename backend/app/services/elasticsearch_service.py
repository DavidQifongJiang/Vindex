"""Opt-in, immutable per-video indexes for retrieval experiments.

Explicit snapshot preparation keeps Elasticsearch out of processing transactions.
One index per snapshot preserves per-video BM25 statistics; this is deliberately
an experimental topology, not a design for millions of videos.
"""
import hashlib
import json
import os

import requests


class IndexNotReady(Exception):
    pass


class ElasticsearchSearch:
    def __init__(self, url=None):
        self.url = (url or os.getenv("ELASTICSEARCH_URL", "")).rstrip("/")
        if not self.url:
            raise IndexNotReady("Elasticsearch is not configured")
        self.session = requests.Session()
        if os.getenv("ELASTICSEARCH_API_KEY"):
            self.session.headers["Authorization"] = "ApiKey " + os.environ["ELASTICSEARCH_API_KEY"]
        self.session.verify = os.getenv("ELASTICSEARCH_CA_CERT") or True

    def close(self):
        self.session.close()

    @staticmethod
    def index_name(video_id, revision):
        digest = hashlib.sha256(json.dumps([video_id, revision]).encode()).hexdigest()
        return "vindex-bm25-v1-" + digest

    def request(self, method, path, **kwargs):
        timeout = kwargs.pop("timeout", (5, 30))
        response = self.session.request(method, self.url + "/" + path, timeout=timeout, **kwargs)
        if response.status_code == 404:
            raise IndexNotReady("Prepare the current video snapshot before Elasticsearch search")
        response.raise_for_status()
        return response.json()

    def prepare(self, video_id, revision, segments):
        index = self.index_name(video_id, revision)
        fingerprint = hashlib.sha256(json.dumps(segments, sort_keys=True).encode()).hexdigest()
        try:
            mapping = self.request("GET", index + "/_mapping")[index]["mappings"]
            meta = mapping.get("_meta", {})
            if meta.get("fingerprint") != fingerprint:
                raise ValueError("Immutable snapshot content changed; use a new revision")
            if meta.get("ready"):
                return index
        except IndexNotReady:
            self.request("PUT", index, json={
                "settings": {"number_of_shards": 1, "number_of_replicas": 0,
                    "analysis": {"analyzer": {"vindex": {"type": "custom", "tokenizer": "vindex", "filter": ["lowercase"]}},
                                 "tokenizer": {"vindex": {"type": "pattern", "pattern": "\\W+"}}},
                    "similarity": {"vindex": {"type": "BM25", "k1": 1.2, "b": .75}}},
                "mappings": {"_meta": {"ready": False, "fingerprint": fingerprint}, "dynamic": "strict", "properties": {
                    "start": {"type": "double"}, "end": {"type": "double"},
                    "text": {"type": "text", "analyzer": "vindex", "similarity": "vindex"}}}})
        for offset in range(0, len(segments), 250):
            lines = []
            for number, segment in enumerate(segments[offset:offset + 250], offset):
                lines.extend([json.dumps({"index": {"_id": str(number)}}),
                              json.dumps({key: segment[key] for key in ("start", "end", "text")})])
            response = self.request("POST", index + "/_bulk", data="\n".join(lines) + "\n",
                                    headers={"Content-Type": "application/x-ndjson"})
            if response.get("errors"):
                raise RuntimeError("Elasticsearch bulk indexing failed; snapshot remains unavailable")
        self.request("POST", index + "/_refresh")
        self.request("PUT", index + "/_mapping", json={"_meta": {"ready": True, "fingerprint": fingerprint}})
        return index

    def search(self, video_id, revision, query, limit=5):
        index = self.index_name(video_id, revision)
        mapping = self.request("GET", index + "/_mapping")[index]["mappings"]
        if not mapping.get("_meta", {}).get("ready"):
            raise IndexNotReady("Elasticsearch snapshot indexing has not completed")
        result = self.request("POST", index + "/_search", json={"size": limit,
            "query": {"match": {"text": {"query": query}}},
            "sort": [{"_score": "desc"}, {"start": "asc"}, {"end": "asc"}]})
        return [dict(hit["_source"], score=hit["_score"]) for hit in result["hits"]["hits"]]

    def delete(self, video_id, revision):
        try:
            self.request("DELETE", self.index_name(video_id, revision))
        except IndexNotReady:
            pass
