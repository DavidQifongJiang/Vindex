import unittest
from benchmarks.score_accepted_candidates import evaluate
from benchmarks.candidate_diagnostics import content_key


class AcceptedCandidatesTest(unittest.TestCase):
    def test_new_text_stays_unknown_and_common_subset_is_paired(self):
        hit = dict(video_id='v', start=0, end=1, text='Known answer')
        other = dict(hit, text='New text')
        labels = {content_key('Why?', hit): {'rating': 2}}
        rows = [dict(query_id='q', query='Why?', query_type='conceptual', method=m, results=[h])
                for m, h in [('baseline', hit), ('new', other)]]
        summary, scores, pending = evaluate(rows, labels)
        self.assertEqual(summary['common_fully_labeled_queries'], 0)
        self.assertEqual(len(pending), 1)
        bound = summary['methods']['new']['all_query_bounds']['hit_at_5']
        self.assertIsNone(bound['value'])
        self.assertEqual((bound['lower_bound'], bound['upper_bound']), (0, 1))

    def test_identity_requires_video_and_times(self):
        hit = dict(video_id='v', start=0, end=1, text='Same text')
        for changed in [dict(hit, video_id='other'), dict(hit, start=.5), dict(hit, end=2)]:
            self.assertNotEqual(content_key('q', hit), content_key('q', changed))

    def test_negative_candidates_excluded_from_quality_not_pending_review(self):
        row = dict(query_id='n', query='Missing?', query_type='no_answer_candidate', method='baseline',
                   results=[dict(video_id='v', start=0, end=1, text='text')])
        positive = dict(row, query_id='p', query='Present?', query_type='conceptual')
        summary, scores, pending = evaluate([row, positive], {})
        self.assertEqual(len(scores), 1)
        self.assertEqual(len(pending), 2)


if __name__ == '__main__':unittest.main()
