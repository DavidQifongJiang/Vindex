import json
from pathlib import Path
import tempfile
import unittest

from benchmarks.analyze_pipeline_experiment import analyze_campaign


class PipelineExperimentAnalysis(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        waves=[]
        for pair in range(3):
            for mode in ['api','direct']:
                name=f'{pair}-{mode}'
                spec={'name':name,'pair':pair,'mode':mode,'worker_state':'warm','fixture':'speech.mp4','concurrency':2,'role':'measured'}
                waves.append(spec);folder=self.root/name
                rows=[]
                for client in range(2):
                    seconds=(5+pair+client)*(0.9 if mode=='direct' else 1)
                    milestone={'outcome':'ready','observed_seconds':seconds,'after_acceptance_seconds':seconds-1}
                    rows.append({'video_id':f'{name}-{client}','mode':mode,'worker_state':'warm','client':client,'bytes':100,
                                 'outcome':'accepted','readiness_outcome':'ready','upload_seconds':1,
                                 'searchable':milestone,'playable':milestone})
                self.write(folder/'spec.json',spec)
                self.write(folder/'browser/run.json',{'network':{'name':'fixture'},'commit':'a'*40,
                    'readiness':{'poll_interval_ms':1000,'probe_timeout_ms':10000,'deadline_seconds':600,'search':{'query':'fixture'}}})
                self.write(folder/'browser/completion.json',{'finished_all_planned_waves':True,'all_attempts_ready':True,'planned_attempts':2})
                self.write(folder/'browser/fixtures.jsonl',{'sha256':'b'*64})
                (folder/'browser/attempts.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
                self.write(folder/'verification.json',{'verified':[{'video_id':r['video_id'],'sha256':'b'*64,'bytes':100,'vectors':1,'single_attempt':True} for r in rows]})
                self.write(folder/'cleanup.json',{'remaining_objects':0,'remaining_vectors':0,'deleted':[{'video_id':r['video_id']} for r in rows]})
        self.write(self.root/'plan.json',{'name':'fixture','waves':waves})
        self.write(self.root/'progress.json',{'finished':True,'completed':[{'wave':w['name']} for w in waves]})

    def write(self,path,value):
        path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value))

    def test_paired_ratios_and_small_sample_gate(self):
        report=analyze_campaign(self.root,iterations=500)
        self.assertEqual(report['unique_video_ids_including_warmups'],12)
        cell=report['cells'][0]
        self.assertAlmostEqual(cell['metrics']['searchable']['p95']['direct_over_api'],0.9)
        self.assertIn('exploratory',cell['gates']['searchable_p95_non_regression'])
        self.assertLess(cell['descriptive_wilson_95pct'][0],0.99)

    def test_incomplete_and_duplicate_accounting_are_rejected(self):
        progress={'finished':False,'completed':[]};self.write(self.root/'progress.json',progress)
        with self.assertRaisesRegex(ValueError,'Incomplete'): analyze_campaign(self.root)
        progress['finished']=True;self.write(self.root/'progress.json',progress)
        with self.assertRaisesRegex(ValueError,'accounting'): analyze_campaign(self.root)

    def test_wrong_stored_bytes_or_missing_cleanup_cannot_pass(self):
        file=self.root/'0-api/verification.json'; data=json.loads(file.read_text());data['verified'][0]['sha256']='c'*64;self.write(file,data)
        with self.assertRaisesRegex(ValueError,'Verification'): analyze_campaign(self.root)
        data['verified'][0]['sha256']='b'*64;self.write(file,data)
        self.write(self.root/'0-api/cleanup.json',{'remaining_objects':1,'remaining_vectors':0,'deleted':[]})
        with self.assertRaisesRegex(ValueError,'cleanup'): analyze_campaign(self.root)

    def test_different_probe_contracts_cannot_be_pooled(self):
        file=self.root/'0-api/browser/run.json';data=json.loads(file.read_text());data['readiness']['poll_interval_ms']=5000;self.write(file,data)
        with self.assertRaisesRegex(ValueError,'contracts'): analyze_campaign(self.root)
