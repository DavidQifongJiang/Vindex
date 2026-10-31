"""Shared, rebuildable Elasticsearch projection of accepted transcript revisions.

Postgres/S3 remain authoritative. A manifest document makes a revision searchable
only after every bulk item has succeeded and the passages have been refreshed.
The separate per-video ElasticsearchSearch class remains an offline experiment.
"""
import hashlib
import json
import os
import re

import requests

from app.services.elasticsearch_service import ElasticsearchSearch, IndexNotReady


class LexicalIndex(ElasticsearchSearch):
    def __init__(self, url=None, index=None):
        super().__init__(url)
        self.index = index or os.getenv("ELASTICSEARCH_INDEX", "vindex-passages-v1")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,199}", self.index):
            self.close()
            raise ValueError("ELASTICSEARCH_INDEX must be a single concrete lowercase index name")

    @staticmethod
    def revision_token(video_id, revision):
        return hashlib.sha256(json.dumps([video_id, revision]).encode()).hexdigest()

    def ensure_index(self):
        try:
            self.request("PUT", self.index, json={
                "settings": {"number_of_shards": 1, "number_of_replicas": 0},
                "mappings": {"dynamic": "strict", "properties": {
                    "kind": {"type": "keyword"}, "video_id": {"type": "keyword"},
                    "revision_token": {"type": "keyword"},
                    "fingerprint": {"type": "keyword"}, "ready": {"type": "boolean"},
                    "count": {"type": "integer"}, "ordinal": {"type": "integer"},
                    "start": {"type": "double"}, "end": {"type": "double"},
                    "text": {"type": "text", "analyzer": "standard"},
                }}})
        except requests.HTTPError as exc:
            if exc.response is None or exc.response.status_code != 400:
                raise
            if exc.response.json().get("error", {}).get("type") != "resource_already_exists_exception":
                raise

    def manifest(self, token):
        return self.request("GET", f"{self.index}/_doc/manifest-{token}")["_source"]

    @staticmethod
    def filters(video_id, token=None):
        result = [{"term": {"video_id": video_id}}]
        if token is not None:
            result.append({"term": {"revision_token": token}})
        return result

    def prepare(self, video_id, revision, segments):
        # Fingerprint only the persisted schema, independent of experimental scores.
        passages = [{key: segment[key] for key in ("start", "end", "text")} for segment in segments]
        fingerprint = hashlib.sha256(json.dumps(passages, sort_keys=True, allow_nan=False).encode()).hexdigest()
        token = self.revision_token(video_id, revision)
        self.ensure_index()
        manifest = {"kind": "manifest", "video_id": video_id, "revision_token": token,
                    "fingerprint": fingerprint, "count": len(passages), "ready": False}
        try:
            current = self.manifest(token)
        except IndexNotReady:
            try:
                self.request("PUT", f"{self.index}/_create/manifest-{token}", json=manifest)
                current = manifest
            except requests.HTTPError as exc:
                if exc.response is None or exc.response.status_code != 409:
                    raise
                current = self.manifest(token)
        if current["fingerprint"] != fingerprint:
            raise ValueError("Accepted transcript content changed without a new revision")
        if current.get("ready"):
            count = self.request("POST", f"{self.index}/_count", json={"query": {"bool": {
                "filter": self.filters(video_id, token) + [{"term": {"kind": "passage"}}]}}})["count"]
            if count == len(passages):
                return token
            self.request("PUT", f"{self.index}/_doc/manifest-{token}", json=manifest)
        for offset in range(0, len(passages), 250):
            lines = []
            for number, passage in enumerate(passages[offset:offset + 250], offset):
                lines.extend([json.dumps({"index": {"_id": f"{token}-{number}"}}),
                    json.dumps(dict(passage, kind="passage", video_id=video_id,
                                    revision_token=token, ordinal=number), allow_nan=False)])
            result = self.request("POST", f"{self.index}/_bulk", data="\n".join(lines) + "\n",
                                  headers={"Content-Type": "application/x-ndjson"})
            if result.get("errors"):
                raise RuntimeError("Lexical indexing failed; the revision is not ready")
        refreshed = self.request("POST", f"{self.index}/_refresh")
        if refreshed.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Lexical index refresh failed")
        self.request("PUT", f"{self.index}/_doc/manifest-{token}", json=dict(manifest, ready=True))
        return token

    def search(self, video_id, revision, query, limit=5):
        token = self.revision_token(video_id, revision)
        if not self.manifest(token).get("ready"):
            raise IndexNotReady("Keyword search is still indexing this video; try again shortly")
        result = self.request("POST", f"{self.index}/_search", params={"allow_partial_search_results": "false"}, json={
            "size": limit, "_source": ["start", "end", "text"],
            "query": {"bool": {"filter": self.filters(video_id, token) + [{"term": {"kind": "passage"}}],
                               "must": [{"match": {"text": query}}]}},
            "sort": [{"_score": "desc"}, {"start": "asc"}, {"ordinal": "asc"}]})
        if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Lexical search did not complete")
        return [dict(hit["_source"], score=hit["_score"]) for hit in result["hits"]["hits"]]

    def delete(self, video_id, revision=None, *, token=None):
        if revision is not None:
            token = self.revision_token(video_id, revision)
        try:
            refreshed = self.request("POST", f"{self.index}/_refresh")
            if refreshed.get("_shards", {}).get("failed", 0):
                raise RuntimeError("Lexical cleanup refresh failed")
            result = self.request("POST", f"{self.index}/_delete_by_query", params={"refresh": "true"},
                json={"query": {"bool": {"filter": self.filters(video_id, token)}}})
        except IndexNotReady:
            return
        if result.get("failures") or result.get("version_conflicts") or result.get("timed_out"):
            raise RuntimeError("Lexical cleanup is incomplete; retry required")

    def revisions(self, after=None, limit=100):
        composite = {"size": limit, "sources": [
            {"video_id": {"terms": {"field": "video_id"}}},
            {"revision_token": {"terms": {"field": "revision_token"}}}]}
        if after:
            composite["after"] = after
        try:
            result = self.request("POST", f"{self.index}/_search", params={"allow_partial_search_results": "false"},
                json={"size": 0, "aggs": {"revisions": {"composite": composite}}})
        except IndexNotReady:
            return [], None
        if result.get("timed_out") or result.get("_shards", {}).get("failed", 0):
            raise RuntimeError("Lexical revision scan did not complete")
        groups = result["aggregations"]["revisions"]
        return [bucket["key"] for bucket in groups["buckets"]], groups.get("after_key")
