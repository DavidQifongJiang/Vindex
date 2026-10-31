import unittest
from benchmarks.context_containment_experiment import expand
from benchmarks.linear_fusion_experiment import fuse


def passage(index):
    return dict(passage_id=str(index), video_id='v', revision_id='r', ordinal=index,
                start=index*30,end=(index+1)*30,text='text'+str(index),score=.5)


class ContextExperimentTests(unittest.TestCase):
    def test_preserves_anchor_and_cap_at_both_video_boundaries(self):
        siblings=[passage(i) for i in range(6)]
        for anchor in siblings:
            for cap in [60,120]:
                result=expand(anchor,siblings,cap)
                self.assertIn(anchor['passage_id'],result['child_ids'])
                self.assertIn(anchor['text'],result['text'])
                self.assertLessEqual(result['end']-result['start'],cap)
                self.assertEqual(result['anchor_start'],anchor['start'])
                self.assertEqual(result['revision_id'],anchor['revision_id'])

    def test_does_not_jump_large_gap(self):
        siblings=[passage(0),dict(passage(1),start=200,end=230)]
        result=expand(siblings[0],siblings,120)
        self.assertEqual(result['child_ids'],['0'])

    def test_normalization_is_invariant_to_positive_affine_score_change(self):
        branches={'semantic':[dict(passage(0),score=.9),dict(passage(1),score=.7)],
                  'lexical':[dict(passage(1),score=10),dict(passage(2),score=2)]}
        first=fuse(branches)
        changed={k:[dict(p,score=p['score']*3+5) for p in v] for k,v in branches.items()}
        second=fuse(changed)
        self.assertEqual([p['passage_id'] for p in first],[p['passage_id'] for p in second])
        for a,b in zip(first,second):self.assertAlmostEqual(a['score'],b['score'])

    def test_flat_and_empty_branches_are_defined(self):
        self.assertEqual(fuse({'semantic':[],'lexical':[]}),[])
        result=fuse({'semantic':[passage(0),passage(1)],'lexical':[]})
        self.assertEqual([p['score'] for p in result],[.5,.5])

    def test_nonfinite_score_rejected(self):
        with self.assertRaises(ValueError):fuse({'semantic':[dict(passage(0),score=float('nan'))]})
