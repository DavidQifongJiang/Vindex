"""Switch the isolated server to upstream TLS only after a completed campaign.

No live deployment changes. The instance and app container are identified by the
benchmark resource journal, and the original dataset remains unchanged.
"""
import argparse
import json
from pathlib import Path

import boto3

if __package__:
    from .provision_upload_bench import validate_resource_journal
else:
    from provision_upload_bench import validate_resource_journal


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--completed-campaign', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.resources.read_text())
    validate_resource_journal(state)
    completed = json.loads((args.completed_campaign / 'campaign.json').read_text())
    if not completed.get('completed') or not completed.get('cells') or any(c.get('exit_code') != 0 for c in completed['cells']):
        raise SystemExit('Finish the running campaign before changing ingress')
    for cell in completed['cells']:
        if Path(cell['name']).name != cell['name']:
            raise SystemExit('Invalid cell identity')
        marker = json.loads((args.completed_campaign / cell['name'] / 'completion.json').read_text())
        if not marker.get('finished_all_planned_waves') or marker['recorded_attempts'] != marker['planned_attempts']:
            raise SystemExit('A comparison cell is incomplete')
    s = boto3.Session(profile_name='default', region_name='us-east-1')
    if s.client('sts').get_caller_identity()['Account'] != '585572506964' or state.get('account') != '585572506964':
        raise SystemExit('Account mismatch')
    instance = s.client('ec2').describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
    if {t['Key']: t['Value'] for t in instance.get('Tags', [])}.get('Name') != state['name'] or not state['name'].startswith('vindex-upload-bench-'):
        raise SystemExit('Instance ownership mismatch')
    bucket = 'vindex-upload-bench-585572506964-' + state['name'].removeprefix('vindex-upload-bench-')
    if state['bucket'] != bucket:
        raise SystemExit('Bucket ownership mismatch')
    folder = args.resources.resolve().parent
    wrapper = Path(__file__).with_name('upload_bench_server.py')
    s.client('s3').upload_file(str(wrapper), bucket, 'bootstrap/server-proxy.py')
    script = f'''#!/bin/bash
set -euo pipefail
cd /opt/upload-bench
test "$(docker exec bench-postgres psql -U bench -d vindex_upload_bench -Atc 'select count(*) from videos;')" = 0
test "$(docker exec bench-postgres psql -U bench -d vindex_upload_bench -Atc 'select count(*) from pipeline_jobs;')" = 0
aws s3 cp s3://{bucket}/bootstrap/server-proxy.py upload_bench_server.py --region us-east-1
docker pull caddy:2
cat > Caddyfile <<'CADDY'
:8443 {{
  tls /tls/cert.pem /tls/key.pem
  reverse_proxy bench-api:8000
}}
CADDY
docker stop bench-api
docker rm bench-api
docker run -d --name bench-proxy --network upload-bench --cpus=0.5 --memory=128m --memory-swap=128m -p 8443:8443 -v /opt/upload-bench/Caddyfile:/etc/caddy/Caddyfile:ro -v /opt/upload-bench/tls:/tls:ro caddy:2
proxy_pid=$(docker inspect bench-proxy --format '{{{{.State.Pid}}}}')
proxy_cgroup=$(cut -d: -f3 /proc/$proxy_pid/cgroup)
test -f /sys/fs/cgroup$proxy_cgroup/cpu.stat
docker run -d --name bench-api --network upload-bench --cpus=0.5 --memory=2g --memory-swap=2g --env-file app.env -v /opt/upload-bench:/bench:ro -v /sys/fs/cgroup$proxy_cgroup:/proxy-cgroup:ro {state['image']} uvicorn upload_bench_server:application --host 0.0.0.0 --port 8000 --no-access-log
docker inspect bench-proxy --format 'proxy_image={{{{.Image}}}}'
docker image inspect caddy:2 --format '{{{{json .RepoDigests}}}}'
docker inspect bench-api --format 'api_cpu_nano={{{{.HostConfig.NanoCpus}}}} api_memory={{{{.HostConfig.Memory}}}}'
echo proxy-configured
'''
    (folder / 'configure-proxy.sh').write_text(script, encoding='utf-8', newline='\n')
    s.client('s3').upload_file(str(folder / 'configure-proxy.sh'), bucket, 'bootstrap/configure-proxy.sh')
    command = s.client('ssm').send_command(InstanceIds=[state['instance']], DocumentName='AWS-RunShellScript',
        Parameters={'commands': [f'aws s3 cp s3://{bucket}/bootstrap/configure-proxy.sh /opt/upload-bench/configure-proxy.sh --region us-east-1',
                                 'bash /opt/upload-bench/configure-proxy.sh'], 'executionTimeout': ['300']},
        Comment='Confirm upload comparison with separate HTTPS termination')['Command']['CommandId']
    (folder / 'proxy-command.txt').write_text(command)
    print(json.dumps({'command': command}))


if __name__ == '__main__':
    main()
