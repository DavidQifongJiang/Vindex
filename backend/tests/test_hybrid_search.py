import math
import os
from uuid import uuid4
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from app.services.hybrid_search import BM25, reciprocal_rank_fusion
from app.services.elasticsearch_service import ElasticsearchSearch, IndexNotReady
from benchmarks.compare_retrieval import evaluate


def segment(text, start=0, score=1):
    return {'text': text, 'start': start, 'end': start + 1, 'score': score}


class HybridSearchTest(unittest.TestCase):
    def test_bm25_formula_and_rare_identifier(self):
        corpus = [segment('alpha alpha'), segment('alpha z913')]
        index = BM25(corpus)
        self.assertEqual(index.search('Z913!')[0]['start'], 0)
        self.assertEqual(index.search('Z913!')[0]['text'], 'alpha z913')
        self.assertAlmostEqual(index.search('z913')[0]['score'], math.log(2))
        self.assertEqual(index.search('absent'), [])
        self.assertEqual(index.search(''), [])
        self.assertEqual(BM25([]).search('anything'), [])

    def test_rrf_fuses_ranks_not_incompatible_scores_and_deduplicates(self):
        a, b, c = segment('a', 0, 10000), segment('b', 1, .01), segment('c', 2)
        results = reciprocal_rank_fusion([a, b, b], [b, c])
        self.assertEqual([s['text'] for s in results], ['b', 'a', 'c'])
        self.assertAlmostEqual(results[0]['score'], 1/62 + 1/61)

    def test_benchmark_preserves_timestamp_rule_and_miss(self):
        label = {'expected_start_seconds': '10', 'expected_end_seconds': '20', 'relevance_window_seconds': '1'}
        self.assertEqual(evaluate([segment('hit', 11)], label)['mrr'], 1)
        self.assertEqual(evaluate([segment('miss', 100)], label)['hit_at_5'], 0)
        self.assertIsNone(evaluate([], label)['timestamp_error_seconds'])


class ElasticsearchTest(unittest.TestCase):
    def setUp(self):
        self.client = ElasticsearchSearch('http://localhost:9200')
        self.addCleanup(self.client.close)

    def test_snapshot_identity_changes_with_video_or_revision(self):
        self.assertNotEqual(self.client.index_name('a', 'r1'), self.client.index_name('b', 'r1'))
        self.assertNotEqual(self.client.index_name('a', 'r1'), self.client.index_name('a', 'r2'))

    def test_partial_bulk_never_marks_snapshot_ready(self):
        with patch.object(self.client, 'request', side_effect=[IndexNotReady(), {}, {'errors': True}]) as request:
            with self.assertRaises(RuntimeError):
                self.client.prepare('v', 'revision', [segment('hello')])
            self.assertEqual(request.call_count, 3)

    def test_search_refuses_incomplete_snapshot(self):
        index = self.client.index_name('v', 'r')
        with patch.object(self.client, 'request', return_value={index: {'mappings': {'_meta': {'ready': False}}}}) as request:
            with self.assertRaises(IndexNotReady):
                self.client.search('v', 'r', 'hello')
            request.assert_called_once()

    def test_successful_prepare_publishes_only_after_refresh(self):
        with patch.object(self.client, 'request', side_effect=[IndexNotReady(), {}, {'errors': False}, {}, {}]) as request:
            self.client.prepare('v', 'r', [segment('hello')])
            self.assertTrue(request.call_args_list[-2].args[1].endswith('/_refresh'))
            self.assertTrue(request.call_args_list[-1].kwargs['json']['_meta']['ready'])


class SearchAuthorizationTest(unittest.TestCase):
    def setUp(self):
        with patch('app.services.storage_service.S3Storage'):
            from app.api import videos
        self.api = videos
        self.video = SimpleNamespace(visibility='private', owner_user_id='owner', status='processed',
            segments_status='completed', segments_path='accepted/segments', embedding_status='completed')

    def test_all_new_modes_check_ownership_before_dependencies(self):
        from fastapi import HTTPException
        for algorithm in ['bm25', 'hybrid', 'elasticsearch_bm25', 'elasticsearch_hybrid']:
            with self.subTest(algorithm=algorithm), patch.object(self.api, 'get_video', return_value=self.video), \
                 patch.object(self.api, 'storage') as storage, patch.object(self.api, 'ElasticsearchSearch') as elastic:
                with self.assertRaises(HTTPException) as caught:
                    self.api.search('v', self.api.SearchRequest(query='q', algorithm=algorithm),
                                    current_user=SimpleNamespace(user_id='intruder'), db=Mock())
                self.assertEqual(caught.exception.status_code, 404)
                storage.read_json.assert_not_called()
                elastic.assert_not_called()

    def test_hybrid_passes_accepted_attempts_to_dense_search(self):
        with patch.object(self.api, 'get_video', return_value=self.video), patch.object(self.api, 'storage') as storage, \
             patch.object(self.api, 'list_search_chunks', return_value=[SimpleNamespace(chunk_index=0, artifact_token='accepted')]), \
             patch.object(self.api, 'encode_text', return_value=[1]), \
             patch.object(self.api, 'search_segments', return_value=[segment('rare')]) as dense:
            storage.read_json.return_value = [segment('rare')]
            result = self.api.search('v', self.api.SearchRequest(query='rare', algorithm='hybrid'),
                current_user=SimpleNamespace(user_id='owner'), db=Mock())
            self.assertEqual(dense.call_args.kwargs['chunk_attempts'], [(0, 'accepted')])
            self.assertEqual(len(result['results']), 1)


@unittest.skipUnless(os.getenv('VINDEX_TEST_ELASTICSEARCH_URL'), 'Requires a dedicated Elasticsearch test instance')
class RealElasticsearchTest(unittest.TestCase):
    def test_snapshot_prepare_search_replay_and_delete(self):
        client = ElasticsearchSearch(os.environ['VINDEX_TEST_ELASTICSEARCH_URL'])
        video_id = 'test-' + uuid4().hex
        corpus = [segment('alpha rareidentifier', 1), segment('alpha ordinary', 2)]
        try:
            first = client.prepare(video_id, 'test-revision', corpus)
            self.assertEqual(first, client.prepare(video_id, 'test-revision', corpus))
            results = client.search(video_id, 'test-revision', 'RAREIDENTIFIER')
            self.assertEqual([r['text'] for r in results], ['alpha rareidentifier'])
            with self.assertRaises(ValueError):
                client.prepare(video_id, 'test-revision', [segment('different')])
            with self.assertRaises(IndexNotReady):
                client.search(video_id, 'different-revision', 'alpha')
            client.delete(video_id, 'test-revision')
            with self.assertRaises(IndexNotReady):
                client.search(video_id, 'test-revision', 'alpha')
        finally:
            client.delete(video_id, 'test-revision')
            client.close()
