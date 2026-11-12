"""Run outside timed intervals, and prove all four worker processes are warm."""
import json
from app.workers.celery_app import celery_app
from architecture_control import architecture

pids = set()
configuration = []
for _ in range(3):
    results = [celery_app.send_task('architecture_warm', queue='search') for _ in range(4)]
    values = [result.get(timeout=120) for result in results]
    pids.update(value['pid'] for value in values)
    configuration.extend(values)
    if len(pids) == 4:
        break
assert len(pids) == 4, 'Could not warm exactly the configured four worker children'
expected = architecture()
assert all(row['architecture'] == expected and row['chunk_seconds'] == (1800 if expected == 'branches' else 300)
           for row in configuration), 'Worker configuration differs from the trial'
print(json.dumps({'pids': sorted(pids), 'configuration': configuration}))
