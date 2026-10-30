import unittest
from unittest.mock import patch,Mock
from benchmarks.gguf_relevance_judge import assess,key
from benchmarks.summarize_before_after500 import calibration,interval


class FullBenchmarkAssessmentTests(unittest.TestCase):
    def response(self,content,finish='stop'):
        response=Mock()
        response.json.return_value={'choices':[{'finish_reason':finish,'message':{'content':content}}]}
        return response

    @patch('benchmarks.gguf_relevance_judge.requests.post')
    def test_missing_grade_never_becomes_zero(self,post):
        post.return_value=self.response('{"grades":[2]}')
        with self.assertRaises(ValueError):assess([{'query':'q','text':'a'},{'query':'q','text':'b'}])
        self.assertEqual(post.call_count,3)

    @patch('benchmarks.gguf_relevance_judge.requests.post')
    def test_rejects_boolean_grade(self,post):
        post.return_value=self.response('{"grades":[true]}')
        with self.assertRaises(ValueError):assess([{'query':'q','text':'a'}])

    @patch('benchmarks.gguf_relevance_judge.requests.post')
    def test_truncated_response_is_not_scored(self,post):
        post.return_value=self.response('{"grades":[2]}',finish='length')
        with self.assertRaises(ValueError):assess([{'query':'q','text':'a'}])

    @patch('benchmarks.gguf_relevance_judge.requests.post')
    def test_judge_does_not_receive_labels_engine_or_rank(self,post):
        post.return_value=self.response('{"grades":[1]}')
        self.assertEqual(assess([dict(query='q',text='a',human_rating=2,mode='hybrid',rank=1)]),[1])
        messages=post.call_args.kwargs['json']['messages']
        self.assertNotIn('human_rating',messages[1]['content'])
        self.assertNotIn('hybrid',messages[1]['content'])
        self.assertNotIn('rank',messages[1]['content'])

    def test_calibration_does_not_hide_positive_label_failure(self):
        result=calibration([dict(human_rating=0,rating=0)]*22+[dict(human_rating=0,rating=2)]+[dict(human_rating=1,rating=0)]*2+[dict(human_rating=1,rating=1)]+[dict(human_rating=1,rating=2)]*2+[dict(human_rating=2,rating=0),dict(human_rating=2,rating=2)])
        self.assertEqual(result['agreement'],.8)
        self.assertEqual(result['grade2_precision'],.25)
        self.assertEqual(result['grade2_recall'],.5)

    def test_same_text_for_different_queries_is_not_same_judgment(self):
        self.assertNotEqual(key('q1','a'),key('q2','a'))

    def test_zero_paired_difference(self):
        self.assertEqual(interval([0]*10),dict(mean=0.,ci95=[0.,0.]))

if __name__=='__main__':unittest.main()
