"""Separate client host; all experiment instructions arrive through its owned bucket."""
import json
from pathlib import Path
import subprocess
import time

from campaign import ROOT, command, write, utc


def publish_recorded(folder, settings):
    # Replay publication after interruption, preserving the original workload result.
    if not (folder/'client-result.json').is_file():
        return False
    bucket = settings['bucket']
    command(['aws', 's3', 'sync', str(folder), f's3://{bucket}/client-evidence/{folder.name}/', '--only-show-errors'], 120)
    command(['aws', 's3', 'cp', str(folder/'client-result.json'), f's3://{bucket}/control/completed/{folder.name}.json', '--only-show-errors'])
    return True


def stats(names):
    rows = {}
    for name in names:
        item = json.loads(command(['docker', 'inspect', name]))[0]
        pid = item['State']['Pid']
        row = {'running': item['State']['Running'], 'oom': item['State']['OOMKilled'],
               'exit_code': item['State']['ExitCode'], 'cpu_nano': item['HostConfig']['NanoCpus'],
               'memory_limit': item['HostConfig']['Memory']}
        if pid:
            group = Path('/sys/fs/cgroup') / Path(f'/proc/{pid}/cgroup').read_text().strip().split(':')[-1].lstrip('/')
            row['cpu'] = dict(line.split() for line in (group/'cpu.stat').read_text().splitlines())
            row['memory_bytes'] = int((group/'memory.current').read_text())
        rows[name] = row
    return rows


def run(spec, settings):
    if 'upload_client_vcpu' in spec:
        assert float(settings.get('upload_client_cpus', '6')) == spec['upload_client_vcpu'], 'Upload client quota differs from registration'
        assert float(settings.get('query_client_cpus', '1')) == spec['query_client_vcpu'], 'Query client quota differs from registration'
    folder = ROOT/'results'/spec['name']; folder.mkdir()
    write(folder/'spec.json', spec)
    remote = '/bench/results/' + spec['name']
    names = ['mixed-uploads', 'mixed-queries']
    for name, script, cpu, memory in [('mixed-uploads', 'run_mixed_uploads.cjs', settings.get('upload_client_cpus', '6'), '10g'),
                                       ('mixed-queries', 'run_mixed_queries.cjs', settings.get('query_client_cpus', '1'), '1g')]:
        subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        command(['docker', 'run', '-d', '--init', '--name', name, '--cpus='+cpu, '--memory='+memory,
                 '--memory-swap='+memory, '--shm-size=1g', '--add-host', 'bench-proxy:'+settings['server_ip'],
                 '-v', str(ROOT)+':/bench', '-w', '/bench', settings['browser_image'],
                 '/bench/runtime/node_modules/node/bin/node', '/bench/backend/benchmarks/'+script, remote])
    ready_deadline = time.monotonic()+240
    while not (folder/'upload-ready.json').exists():
        if time.monotonic() > ready_deadline or not all(row['running'] for row in stats(names).values()):
            raise RuntimeError('Client preparation failed')
        time.sleep(2)
    write(folder/'go.json', {'start_epoch': time.time()+5, 'recorded_at': utc()})
    write(folder/'before.json', stats(names))
    deadline = time.monotonic()+spec['baseline_seconds']+spec['duration_seconds']+spec['recovery_seconds']+spec['upload_timeout_seconds']+60
    with (folder/'resources.jsonl').open('w') as stream:
        while time.monotonic() < deadline:
            observation = stats(names)
            stream.write(json.dumps({'epoch': time.time(), 'containers': observation})+'\n'); stream.flush()
            if not any(row['running'] for row in observation.values()): break
            time.sleep(5)
        else: raise RuntimeError('Client deadline exceeded')
    for name in names:
        with (folder/(name+'.log')).open('w') as stream:
            subprocess.run(['docker', 'logs', name], stdout=stream, stderr=subprocess.STDOUT, timeout=15)
    write(folder/'after.json', stats(names))
    assert all(row['exit_code'] == 0 and not row['oom'] for row in stats(names).values()), 'Generator exited unsuccessfully'


def main():
    settings = json.loads((ROOT/'run-settings.json').read_text())
    bucket = settings['bucket']; seen = set()
    def put(path, key): command(['aws', 's3', 'cp', str(path), f's3://{bucket}/'+key, '--only-show-errors'])
    write(ROOT/'client-ready.json', {'ready': True, 'at': utc()}); put(ROOT/'client-ready.json', 'control/client-ready.json')
    while True:
        output = subprocess.run(['aws', 's3', 'cp', f's3://{bucket}/control/request.json', '-', '--only-show-errors'], capture_output=True, text=True, timeout=30)
        if output.returncode: time.sleep(2); continue
        spec = json.loads(output.stdout)
        if spec['name'] in seen: time.sleep(2); continue
        seen.add(spec['name'])
        if publish_recorded(ROOT/'results'/spec['name'], settings):
            continue
        error = None
        try: run(spec, settings)
        except Exception as exc:
            error = type(exc).__name__
            subprocess.run(['docker', 'stop', '-t', '10', 'mixed-uploads', 'mixed-queries'], capture_output=True, timeout=40)
        folder = ROOT/'results'/spec['name']
        write(folder/'client-result.json', {'finished': error is None, 'error_type': error, 'at': utc()})
        publish_recorded(folder, settings)


if __name__ == '__main__':
    main()
