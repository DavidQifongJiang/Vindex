import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from benchmarks.analyze_pipeline_experiment import analyze_campaign


class PipelineExperimentAnalysis(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        waves=[]
        for pair in range(4):
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
        self.assertEqual(report['unique_video_ids_including_warmups'],16)
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

    def test_pipeline_intervals_preserve_clients_and_matching_wave_pairs(self):
        # Large common wave effects and different route effects expose both ways
        # of accidentally breaking the paired-cluster sampling contract.
        api=[[10,11],[100,110],[20,22],[200,220]]
        direct=[[30,33],[50,55],[50,55],[240,264]]
        for mode,values in [('api',api),('direct',direct)]:
            for pair in range(4):
                file=self.root/f'{pair}-{mode}/browser/attempts.jsonl'
                rows=[json.loads(line) for line in file.read_text().splitlines()]
                for row,seconds in zip(rows,values[pair]):
                    for branch in ['searchable','playable']:
                        row[branch].update(observed_seconds=seconds,after_acceptance_seconds=seconds-1)
                file.write_text('\n'.join(json.dumps(row) for row in rows))
        iterations=2000
        indices=np.random.default_rng(20261110).integers(0,4,size=(iterations,4))
        def nearest(values,percent):
            import math
            return sorted(values)[math.ceil(len(values)*percent)-1]
        def bounds(pairs_a,pairs_b):
            ratios=[]
            for ia,ib in zip(pairs_a,pairs_b):
                a=[v for i in ia for v in api[i]]
                b=[v for i in ib for v in direct[i]]
                ratios.append(nearest(b,.95)/nearest(a,.95))
            return [nearest(ratios,.025),nearest(ratios,.975)]
        expected=bounds(indices,indices)
        actual=analyze_campaign(self.root,iterations=iterations)['cells'][0]['metrics']['searchable']['p95']['ratio_95pct_paired_wave_bootstrap']
        self.assertEqual(actual,expected)
        unpaired=np.random.default_rng(23).integers(0,4,size=(iterations,4))
        self.assertNotEqual(expected,bounds(indices,unpaired))
        flat_indices=np.random.default_rng(20261110).integers(0,8,size=(iterations,8))
        flat_a=sum(api,[]);flat_b=sum(direct,[])
        wrong=[nearest([flat_b[i] for i in row],.95)/nearest([flat_a[i] for i in row],.95) for row in flat_indices]
        self.assertNotEqual(expected,[nearest(wrong,.025),nearest(wrong,.975)])

    def test_warmups_are_excluded_and_worker_cohorts_stay_separate(self):
        plan=json.loads((self.root/'plan.json').read_text())
        for spec in plan['waves']:
            if spec['pair']==0: spec['role']='warmup'
            if spec['pair']==1: spec['worker_state']='cold'
            folder=self.root/spec['name'];self.write(folder/'spec.json',spec)
            file=folder/'browser/attempts.jsonl'
            rows=[json.loads(line) for line in file.read_text().splitlines()]
            for row in rows: row['worker_state']=spec['worker_state']
            file.write_text('\n'.join(json.dumps(row) for row in rows))
        self.write(self.root/'plan.json',plan)
        report=analyze_campaign(self.root,iterations=100)
        self.assertEqual(report['excluded_warmups'],4)
        self.assertEqual(report['unique_video_ids_including_warmups'],16)
        self.assertEqual({c['worker_state']:c['attempts_per_mode'] for c in report['cells']},{'cold':2,'warm':4})

    def test_failed_attempt_prevents_successful_only_comparison(self):
        file=self.root/'0-api/browser/attempts.jsonl'
        rows=[json.loads(line) for line in file.read_text().splitlines()]
        rows[0]['readiness_outcome']='failed'
        file.write_text('\n'.join(json.dumps(row) for row in rows))
        original=file.read_bytes()
        with self.assertRaisesRegex(ValueError,'Failed attempts retained'):
            analyze_campaign(self.root)
        self.assertEqual(file.read_bytes(),original)
