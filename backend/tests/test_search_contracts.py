from dataclasses import FrozenInstanceError
from hashlib import sha256
import json
import unittest
from uuid import UUID

from app.services.hybrid_search import reciprocal_rank_fusion
from app.services.search_contracts import (
    LibrarySearchQuery, Passage, Projection, TranscriptSnapshot, canonical_uuid, eligible_projections,
    passage_id, physical_point_id, seconds_to_ms,
)
from app.services.search_policy import search_outcome


VIDEO = "11111111-1111-4111-8111-111111111111"
OTHER = "22222222-2222-4222-8222-222222222222"
REVISION = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
NEW_REVISION = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ATTEMPT = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
REPLACEMENT = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"


def hit(video=VIDEO, revision=REVISION, ordinal=0, text="Fibonacci heap"):
    return Passage(video, revision, ordinal, 1000, 2000, text).as_hit()


class PassageContractTest(unittest.TestCase):
    def test_library_query_is_bounded_and_owner_scope_is_not_client_selectable(self):
        self.assertEqual(LibrarySearchQuery(" heap ").query, "heap")
        self.assertEqual(LibrarySearchQuery("heap").mode, "semantic")
        for kwargs in [dict(query=" "), dict(query="x" * 501), dict(query="heap", limit=0),
                       dict(query="heap", limit=21), dict(query="heap", limit=True),
                       dict(query="heap", scope="all_users"), dict(query="heap", mode="other")]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                LibrarySearchQuery(**kwargs)

    def test_identity_normalization_and_scoping(self):
        base = passage_id(VIDEO, REVISION, 0)
        self.assertEqual(base, passage_id(UUID(VIDEO), REVISION.upper(), 0))
        self.assertEqual(len({base, passage_id(OTHER, REVISION, 0),
            passage_id(VIDEO, NEW_REVISION, 0), passage_id(VIDEO, REVISION, 1)}), 4)
        for ordinal in [-1, True, 1.0, "1"]:
            with self.subTest(ordinal=ordinal), self.assertRaises(ValueError):
                passage_id(VIDEO, REVISION, ordinal)
        with self.assertRaises(ValueError):
            canonical_uuid("video-not-a-uuid")

    def test_attempt_isolation_and_deterministic_replay(self):
        logical = passage_id(VIDEO, REVISION, 0)
        first = physical_point_id(ATTEMPT, logical)
        self.assertEqual(first, physical_point_id(ATTEMPT, logical))
        self.assertNotEqual(first, physical_point_id(REPLACEMENT, logical))
        self.assertEqual(UUID(first).version, 5)

    def test_legacy_rounding_is_half_up(self):
        self.assertEqual(seconds_to_ms(1.2345), 1235)
        self.assertEqual(seconds_to_ms(1.2344), 1234)
        self.assertEqual(seconds_to_ms(0), 0)
        for value in [True, "1.0", None, float("nan"), float("inf"), -0.1, 1e100]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                seconds_to_ms(value)

    def test_snapshot_preserves_order_duplicates_and_exact_source_checksum(self):
        raw = b'[{"text":"same", "start":1, "end":2},{"text":"same", "start":1, "end":2}]'
        snapshot = TranscriptSnapshot.from_manifest(VIDEO, REVISION, raw,
            expected_sha256=sha256(raw).hexdigest())
        self.assertEqual(len(snapshot.passages), 2)
        self.assertNotEqual(snapshot.passages[0].passage_id, snapshot.passages[1].passage_id)
        self.assertEqual(snapshot, TranscriptSnapshot.from_manifest(VIDEO, REVISION, raw))
        with self.assertRaises(FrozenInstanceError):
            snapshot.passages[0].text = "mutated"
        with self.assertRaises(ValueError):
            TranscriptSnapshot.from_manifest(VIDEO, REVISION, raw, expected_sha256="0" * 64)

    def test_empty_transcript_is_valid_but_malformed_sources_are_not(self):
        self.assertEqual(TranscriptSnapshot.from_manifest(VIDEO, REVISION, b'[]').passages, ())
        sources = [b'{}', b'[null]', b'[{"start":0,"end":1}]',
            b'[{"start":0,"end":1,"text":""}]',
            b'[{"start":NaN,"end":1,"text":"x"}]',
            b'[{"start":0,"start":1,"end":2,"text":"x"}]',
            b'[{"start":1.0004,"end":1.0003,"text":"x"}]']
        for raw in sources:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                TranscriptSnapshot.from_manifest(VIDEO, REVISION, raw)

    def test_snapshot_cannot_mix_videos_revisions_or_ordinals(self):
        for candidate in [Passage(OTHER, REVISION, 0, 0, 1, "x"),
                          Passage(VIDEO, NEW_REVISION, 0, 0, 1, "x"),
                          Passage(VIDEO, REVISION, 1, 0, 1, "x")]:
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                TranscriptSnapshot(VIDEO, REVISION, "0" * 64, (candidate,))

    def test_hit_requires_full_identity_and_exact_canonical_time(self):
        for field in ["video_id", "revision_id", "passage_id", "ordinal"]:
            value = hit()
            del value[field]
            with self.subTest(field=field), self.assertRaises(ValueError):
                Passage.from_hit(value)
        for value in [dict(hit(), passage_id=ATTEMPT), dict(hit(), start=1.0004)]:
            with self.assertRaises(ValueError):
                Passage.from_hit(value)


class CanonicalFusionTest(unittest.TestCase):
    def test_identical_passages_from_different_videos_survive(self):
        results = reciprocal_rank_fusion([hit(), hit(OTHER)], [hit(OTHER), hit()])
        self.assertEqual(len(results), 2)
        self.assertEqual({r["video_id"] for r in results}, {VIDEO, OTHER})
        self.assertEqual({r["revision_id"] for r in results}, {REVISION})

    def test_same_identity_fuses_and_ignores_incomparable_scores(self):
        a, b = hit(), hit(ordinal=1)
        results = reciprocal_rank_fusion([dict(a, score=1000), b], [dict(b, score=.01), a])
        self.assertEqual(len(results), 2)
        self.assertAlmostEqual(results[0]["score"], 1/61 + 1/62)
        self.assertEqual(results, reciprocal_rank_fusion([a, b], [b, a]))

    def test_duplicates_do_not_add_votes(self):
        results = reciprocal_rank_fusion([hit(), hit()], [hit()])
        self.assertEqual(len(results), 1)
        self.assertAlmostEqual(results[0]["score"], 2/61)

    def test_conflicting_text_or_revision_is_rejected(self):
        for bad in [hit(text="conflicting"), hit(revision=NEW_REVISION)]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                reciprocal_rank_fusion([hit()], [bad])
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([hit(), hit(text="conflicting")])

    def test_partial_identity_and_mixed_legacy_data_are_rejected(self):
        legacy = {"start": 1, "end": 2, "text": "Fibonacci heap"}
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([hit()], [legacy])
        with self.assertRaises(ValueError):
            reciprocal_rank_fusion([dict(legacy, video_id=VIDEO)])
        self.assertEqual(len(reciprocal_rank_fusion([legacy], [legacy])), 1)

    def test_result_order_is_deterministic_and_limits_validated(self):
        self.assertEqual(reciprocal_rank_fusion([hit(), hit(OTHER)], k=0),
                         reciprocal_rank_fusion([hit(), hit(OTHER)], k=0))
        self.assertEqual(reciprocal_rank_fusion([hit()], limit=0), [])
        for bad in [-1, float("inf"), float("nan"), True]:
            with self.assertRaises(ValueError):
                reciprocal_rank_fusion([hit()], k=bad)


class EligibilityTest(unittest.TestCase):
    def projection(self, **changes):
        fields = dict(video_id=VIDEO, revision_id=REVISION, projection_id=ATTEMPT,
                      backend="lexical", generation="v1", state="ready")
        return Projection(**(fields | changes))

    def select(self, projections):
        return eligible_projections(projections, current_revisions={VIDEO: REVISION},
                                    backend="lexical", generation="v1")

    def test_only_authorized_current_ready_generation_is_eligible(self):
        ready = self.projection()
        inputs = [ready, self.projection(video_id=OTHER), self.projection(revision_id=NEW_REVISION),
                  self.projection(generation="v2"), self.projection(backend="semantic")]
        inputs.extend(self.projection(state=state) for state in ["pending", "indexing", "retrying", "failed", "obsolete"])
        self.assertEqual(self.select(inputs), (ready,))

    def test_ambiguous_accepted_state_fails_closed(self):
        with self.assertRaises(ValueError):
            self.select([self.projection(), self.projection(projection_id=REPLACEMENT)])
        self.assertEqual(len(self.select([self.projection(), self.projection()])), 1)

    def test_duplicate_projection_id_across_videos_fails_closed(self):
        with self.assertRaises(ValueError):
            eligible_projections([self.projection(), self.projection(video_id=OTHER)],
                current_revisions={VIDEO: REVISION, OTHER: REVISION}, backend="lexical", generation="v1")


class OutcomePolicyTest(unittest.TestCase):
    def test_processing_and_terminal_failure_have_distinct_notices(self):
        processing = search_outcome("hybrid", video_ids=[VIDEO], processing_ids=[VIDEO])
        failed = search_outcome("hybrid", video_ids=[VIDEO], indexing_failed_ids=[VIDEO])
        self.assertIn("video_processing", processing.notices)
        self.assertNotIn("indexing_failed", processing.notices)
        self.assertIn("indexing_failed", failed.notices)
        self.assertEqual(failed.as_dict()["coverage"]["indexing_failed_videos"], 1)
        with self.assertRaises(ValueError):
            search_outcome("hybrid", video_ids=[VIDEO], semantic_ids=[VIDEO],
                           statuses={"semantic": "ok"}, processing_ids=[VIDEO])

    def test_full_hybrid_and_json_contract(self):
        outcome = search_outcome("hybrid", video_ids=[VIDEO], semantic_ids=[VIDEO], lexical_ids=[VIDEO],
                                 statuses={"semantic": "ok", "lexical": "ok"})
        self.assertEqual((outcome.http_status, outcome.effective_mode, outcome.degraded), (200, "hybrid", False))
        self.assertTrue(outcome.coverage_complete)
        self.assertEqual(json.loads(json.dumps(outcome.as_dict()))["requested_mode"], "hybrid")

    def test_partial_coverage_and_failure_are_distinct(self):
        outcome = search_outcome("hybrid", video_ids=[VIDEO, OTHER], semantic_ids=[VIDEO, OTHER], lexical_ids=[VIDEO],
                                 statuses={"semantic": "ok", "lexical": "ok"})
        self.assertFalse(outcome.coverage_complete)
        self.assertFalse(outcome.degraded)
        self.assertEqual(outcome.either_eligible, 2)
        self.assertEqual(outcome.notices, ("library_indexing_incomplete",))

    def test_each_surviving_branch_can_return_success_even_without_matches(self):
        for failure in ["failed", "timeout"]:
            for survivor in ["semantic", "lexical"]:
                statuses = {"semantic": failure, "lexical": failure} | {survivor: "ok"}
                outcome = search_outcome("hybrid", video_ids=[VIDEO], semantic_ids=[VIDEO], lexical_ids=[VIDEO], statuses=statuses)
                self.assertEqual((outcome.http_status, outcome.effective_mode, outcome.degraded), (200, survivor, True))
                self.assertTrue(outcome.coverage_complete)

    def test_one_ready_branch_can_serve_without_claiming_outage(self):
        outcome = search_outcome("hybrid", video_ids=[VIDEO], semantic_ids=[VIDEO], statuses={"semantic": "ok"})
        self.assertEqual(outcome.effective_mode, "semantic")
        self.assertEqual(outcome.lexical_status, "not_ready")
        self.assertFalse(outcome.degraded)

    def test_no_ready_video_vs_empty_library(self):
        pending = search_outcome("hybrid", video_ids=[VIDEO])
        empty = search_outcome("hybrid", video_ids=[])
        self.assertEqual(pending.http_status, 409)
        self.assertEqual(empty.http_status, 200)
        self.assertTrue(empty.coverage_complete)
        self.assertEqual(empty.effective_mode, "none")

    def test_all_executable_branches_failed(self):
        for lexical_ids, statuses in [([VIDEO], {"semantic": "timeout", "lexical": "failed"}),
                                     ([], {"semantic": "timeout"})]:
            outcome = search_outcome("hybrid", video_ids=[VIDEO], semantic_ids=[VIDEO],
                                     lexical_ids=lexical_ids, statuses=statuses)
            self.assertEqual(outcome.http_status, 503)
            self.assertEqual(outcome.effective_mode, "none")

    def test_single_mode_does_not_fall_back_or_require_other_coverage(self):
        outcome = search_outcome("lexical", video_ids=[VIDEO], lexical_ids=[VIDEO], statuses={"lexical": "ok"})
        self.assertEqual(outcome.semantic_status, "not_requested")
        self.assertTrue(outcome.coverage_complete)
        failed = search_outcome("lexical", video_ids=[VIDEO], lexical_ids=[VIDEO], semantic_ids=[VIDEO], statuses={"lexical": "failed"})
        self.assertEqual(failed.http_status, 503)

    def test_inconsistent_inputs_are_rejected(self):
        cases = [dict(video_ids=[VIDEO], semantic_ids=[OTHER]),
                 dict(video_ids=[VIDEO], semantic_ids=[VIDEO]),
                 dict(video_ids=[VIDEO], statuses={"lexical": "ok"}),
                 dict(video_ids=[VIDEO], statuses={"other": "failed"})]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                search_outcome("hybrid", **kwargs)
