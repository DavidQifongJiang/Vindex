import copy
import unittest

from benchmarks.audit_pipeline_experiment import LIMITS, audit_runtime


class PipelineRuntimeAuditTests(unittest.TestCase):
    def setUp(self):
        self.before={'state':{'videos':[],'active':0},'queues':{'search':0,'playback':0,'unacked':0},
            'containers':{name:{'id':name,'started_at':'fixed','image_id':'pinned','running':True,'oom_killed':False,
                'cpu_nano':int(cpu*1e9),'memory_bytes':mib*1024**2,'cpu':{'usage_usec':'1000000'}} for name,(cpu,mib) in LIMITS.items()}}
        self.after=copy.deepcopy(self.before['containers'])
        for value in self.after.values(): value['cpu']['usage_usec']='2000000'

    def test_stable_capacity_and_empty_queues_pass(self):
        self.assertEqual(audit_runtime(self.before,self.after),{n:1.0 for n in LIMITS})

    def test_runtime_restart_or_memory_failure_cannot_pass(self):
        for field,value in [('started_at','restarted'),('running',False),('oom_killed',True)]:
            after=copy.deepcopy(self.after);after['bench-search'][field]=value
            with self.subTest(field=field), self.assertRaises(ValueError): audit_runtime(self.before,after)

    def test_equal_but_wrong_capacity_and_leftover_work_are_rejected(self):
        before=copy.deepcopy(self.before);after=copy.deepcopy(self.after)
        before['containers']['bench-api']['cpu_nano']=after['bench-api']['cpu_nano']=2000000000
        with self.assertRaisesRegex(ValueError,'capacity'): audit_runtime(before,after)
        before=copy.deepcopy(self.before);before['queues']['unacked']=1
        with self.assertRaisesRegex(ValueError,'empty'): audit_runtime(before,self.after)
