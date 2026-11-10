"""Run predeclared comparison cells sequentially; retain partial evidence."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--node', type=Path, required=True)
    args = parser.parse_args()
    plan = json.loads(args.manifest.read_text(encoding='utf-8'))
    output = args.output.resolve()
    output.mkdir()  # Refuse to overwrite evidence.
    if not plan['cells'] or len({c['name'] for c in plan['cells']}) != len(plan['cells']):
        raise SystemExit('Require uniquely named planned cells')
    if any(not c['name'].replace('-', '').isalnum() for c in plan['cells']):
        raise SystemExit('Cell names must be plain path segments')
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'pid': os.getpid(), 'cells': [], 'completed': False}
    def persist():
        (output / 'campaign.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    persist()
    for cell in plan['cells']:
        entry = {'name': cell['name'], 'started_at': datetime.now(timezone.utc).isoformat()}
        report['cells'].append(entry); persist()
        print(json.dumps({'event': 'cell_started', 'name': cell['name']}), flush=True)
        command = [str(args.node.resolve()), str(Path(__file__).with_name('run_upload_comparison.cjs')),
                   '--manifest', str(Path(cell['manifest']).resolve()), '--output', str(output / cell['name'])]
        with (output / (cell['name'] + '.log')).open('w', encoding='utf-8') as log:
            child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                     encoding='utf-8', env={**os.environ, 'VINDEX_BROWSER_CHANNEL': 'msedge'})
            entry['pid'] = child.pid; persist()
            for line in child.stdout:
                log.write(line); log.flush()
                print(line.rstrip(), flush=True)
            entry['exit_code'] = child.wait()
        entry['finished_at'] = datetime.now(timezone.utc).isoformat(); persist()
        if entry['exit_code']:
            raise SystemExit('Campaign stopped at failed cell; all evidence retained')
    report['completed'] = True
    report['finished_at'] = datetime.now(timezone.utc).isoformat(); persist()
    print(json.dumps({'event': 'campaign_complete', 'cells': len(report['cells'])}), flush=True)


if __name__ == '__main__':
    main()
