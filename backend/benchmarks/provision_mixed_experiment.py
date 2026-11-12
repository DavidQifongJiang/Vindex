"""Two-host extension of the journaled isolated experiment; never changes shared services."""
import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import time

import boto3
import provision_pipeline_experiment as base

CLIENT_TYPE = 'c7i.2xlarge'


def save(path, value): path.write_text(json.dumps(value, indent=2)+'\n', encoding='utf-8')


def invoke(action, folder):
    subprocess.run([sys.executable, str(Path(base.__file__)), action, '--output', str(folder), '--account', base.ACCOUNT], check=True)


def prepare(folder):
    invoke('prepare', folder)
    journal = json.loads((folder/'resources.json').read_text())
    journal.update(experiment='mixed-upload-search', client_type=CLIENT_TYPE, maximum_host_minutes=300,
                   maximum_compute_hours=5, estimated_client_hourly_usd=0.357)
    save(folder/'resources.json', journal)
    source = folder/'bootstrap.tar.gz'; target = folder/'mixed-bootstrap.tar.gz'
    hashes = json.loads((folder/'source-hashes.json').read_text())
    original = (Path(base.__file__).parent/'pipeline_experiment/bootstrap.sh').read_text()
    assert original.count('-p 127.0.0.1:8443:8443') == 1
    original = original.replace('-p 127.0.0.1:8443:8443', '-p 0.0.0.0:8443:8443')
    prefix = '''#!/bin/bash
set -euo pipefail
systemctl stop pipeline-expiry.timer || true
remaining=$((17100 - ${SECONDS:-0} - $(cut -d. -f1 /proc/uptime)))
test "$remaining" -gt 300
systemd-run --unit=mixed-expiry --on-active="${remaining}s" /bin/bash -c 'systemctl stop mixed-campaign.service mixed-seed.service; docker stop -t 10 bench-browser bench-dispatcher bench-search bench-playback >/dev/null 2>&1; cd /opt/vindex-experiment; timeout 120 /usr/bin/python3.11 campaign.py --snapshot automatic-expiry; /usr/sbin/poweroff'
systemd-run --unit=mixed-hard-stop --on-active="$((remaining + 300))s" /usr/sbin/poweroff
'''
    bootstrap = prefix + original.removeprefix('#!/bin/bash\n')
    additions = {'bootstrap.sh': bootstrap.encode()}
    for name in ['mixed_load_core.cjs', 'run_mixed_uploads.cjs', 'run_mixed_queries.cjs']:
        additions['backend/benchmarks/'+name] = Path(base.__file__).with_name(name).read_bytes()
    additions['fixtures/speech-80s.mp4'] = (base.ROOT/'.review-cache/pipeline-experiments/fixtures/speech-80s.mp4').read_bytes()
    with tarfile.open(source, 'r:gz') as old, tarfile.open(target, 'w:gz') as new:
        for member in old.getmembers():
            if member.name in additions: continue
            new.addfile(member, old.extractfile(member) if member.isfile() else None)
        for name, data in additions.items():
            info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o600
            new.addfile(info, io.BytesIO(data)); hashes[name] = hashlib.sha256(data).hexdigest()
    target.replace(source)
    save(folder/'source-hashes.json', hashes)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare', 'create', 'remote', 'poll', 'download', 'fetch', 'inventory'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--role', choices=['server', 'client'], default='server')
    parser.add_argument('--script', type=Path)
    parser.add_argument('--command')
    parser.add_argument('--key')
    parser.add_argument('--client-type', choices=['c7i.2xlarge', 'm7i.xlarge'], default=CLIENT_TYPE)
    args = parser.parse_args(); folder = args.output.resolve()
    if args.action == 'prepare': prepare(folder); return
    session = boto3.Session(profile_name='default', region_name=base.REGION)
    assert session.client('sts').get_caller_identity()['Account'] == base.ACCOUNT
    ec2, s3, ssm = session.client('ec2'), session.client('s3'), session.client('ssm')
    if args.action == 'inventory':
        folder.mkdir(parents=True, exist_ok=True)
        prices = {}
        for instance_type in ['m7i.2xlarge', args.client_type]:
            page = session.client('pricing').get_products(ServiceCode='AmazonEC2', Filters=[
                {'Type': 'TERM_MATCH', 'Field': key, 'Value': value} for key, value in
                {'instanceType': instance_type, 'location': 'US East (N. Virginia)', 'operatingSystem': 'Linux',
                 'tenancy': 'Shared', 'preInstalledSw': 'NA', 'capacitystatus': 'Used'}.items()])
            values = []
            for entry in page['PriceList']:
                item = json.loads(entry)
                for term in item.get('terms', {}).get('OnDemand', {}).values():
                    for dim in term['priceDimensions'].values():
                        if dim['unit'] == 'Hrs': values.append(float(dim['pricePerUnit']['USD']))
            assert values and len(set(values)) == 1; prices[instance_type] = values[0]
        shared = ec2.describe_instances(InstanceIds=['i-010d46b34dc600bc8'])['Reservations'][0]['Instances'][0]
        ecs = session.client('ecs').describe_services(cluster='vindex', services=['vindex-api', 'vindex-search-worker', 'vindex-playback-worker'])
        report = {'at': datetime.now(timezone.utc).isoformat(), 'prices_usd_per_hour': prices,
            'shared_host': shared['State']['Name'], 'services': [{'name': s['serviceName'], 'desired': s['desiredCount'], 'running': s['runningCount']} for s in ecs['services']]}
        save(folder/'inventory-before.json', report); print(json.dumps(report)); return
    state = json.loads((folder/'resources.json').read_text()); base.validate_state(state)
    assert state['experiment'] == 'mixed-upload-search'
    if args.action == 'create':
        assert not state.get('client_instance'), 'Both hosts already exist'
        if not state.get('instance'):
            invoke('create', folder)
        state = json.loads((folder/'resources.json').read_text())
        group = state['security_group']
        observed_group = ec2.describe_security_groups(GroupIds=[group])['SecurityGroups'][0]
        assert observed_group['GroupName'] == state['name'] and observed_group['VpcId'] == base.VPC
        rule = {'IpProtocol': 'tcp', 'FromPort': 8443, 'ToPort': 8443,
                'UserIdGroupPairs': [{'GroupId': group, 'Description': 'Only this isolated client and server'}]}
        if not observed_group['IpPermissions']:
            ec2.authorize_security_group_ingress(GroupId=group, IpPermissions=[rule])
        else:
            assert len(observed_group['IpPermissions']) == 1
            existing = observed_group['IpPermissions'][0]
            assert existing['FromPort'] == existing['ToPort'] == 8443 and not existing['IpRanges'] and not existing['Ipv6Ranges']
            assert [item['GroupId'] for item in existing['UserIdGroupPairs']] == [group]
        for _ in range(60):
            server = ec2.describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
            assert {t['Key']: t['Value'] for t in server['Tags']}.get('Name') == state['name']
            if server.get('PrivateIpAddress') and server.get('BlockDeviceMappings'): break
            time.sleep(1)
        else: raise RuntimeError('Server metadata did not become available; journal retained')
        state['server_ip'] = server['PrivateIpAddress']; state['volume'] = server['BlockDeviceMappings'][0]['Ebs']['VolumeId']
        save(folder/'resources.json', state)
        settings = json.loads((folder/'run-settings.json').read_text())
        settings.update(server_ip=state['server_ip'], browser_image=base.IMAGES['BROWSER_IMAGE'])
        if args.client_type == 'm7i.xlarge':
            settings.update(upload_client_cpus='3', query_client_cpus='.5')
        save(folder/'client-settings.json', settings)
        s3.upload_file(str(folder/'client-settings.json'), state['bucket'], 'bootstrap/client-settings.json')
        script = f'''#!/bin/bash
set -euo pipefail
systemd-run --unit=mixed-client-expiry --on-active=285m /bin/bash -c 'systemctl stop mixed-client.service; docker stop -t 10 mixed-uploads mixed-queries >/dev/null 2>&1; timeout 120 aws s3 sync /opt/vindex-experiment/results s3://{state["bucket"]}/client-expiry/ --only-show-errors; /usr/sbin/poweroff'
systemd-run --unit=mixed-hard-stop --on-active=290m /usr/sbin/poweroff
dnf install -y docker python3.11
systemctl enable --now docker
mkdir -p /opt/vindex-experiment/results
cd /opt/vindex-experiment
aws s3 cp s3://{state["bucket"]}/bootstrap/bootstrap.tar.gz bootstrap.tar.gz --only-show-errors
tar -xzf bootstrap.tar.gz
aws s3 cp s3://{state["bucket"]}/bootstrap/client-settings.json run-settings.json --only-show-errors
source runtime.env
docker pull "$BROWSER_IMAGE"
docker run --rm -v /opt/vindex-experiment:/bench "$BROWSER_IMAGE" npm install --no-audit --no-fund --prefix /bench/runtime node@22.23.4
python3.11 - <<'PY'
import json
from pathlib import Path
p=json.loads(Path('private.json').read_text())
Path('session.json').write_text(json.dumps({{'cookies':[{{'name':'vindex_bench','value':p['token'],'domain':'bench-proxy','path':'/','expires':-1,'httpOnly':True,'secure':True,'sameSite':'Lax'}}],'origins':[]}}))
PY
chmod 600 private.json session.json app.env postgres.env
cat > /etc/systemd/system/mixed-client.service <<'UNIT'
[Unit]
After=docker.service
[Service]
Type=simple
WorkingDirectory=/opt/vindex-experiment
ExecStart=/usr/bin/python3.11 /opt/vindex-experiment/mixed_client.py
StandardOutput=append:/opt/vindex-experiment/results/agent.log
StandardError=append:/opt/vindex-experiment/results/agent.log
TimeoutStopSec=15
UNIT
systemctl daemon-reload
systemctl start mixed-client.service
'''
        tags = [{'Key': 'Project', 'Value': 'vindex-pipeline-experiment'}, {'Key': 'Name', 'Value': state['name']}, {'Key': 'ExperimentRole', 'Value': 'client'}]
        ami = ssm.get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
        item = ec2.run_instances(ImageId=ami, InstanceType=args.client_type, MinCount=1, MaxCount=1, IamInstanceProfile={'Name': state['name']}, UserData=script,
            InstanceInitiatedShutdownBehavior='stop', MetadataOptions={'HttpTokens': 'required', 'HttpPutResponseHopLimit': 2},
            NetworkInterfaces=[{'DeviceIndex': 0, 'SubnetId': base.SUBNET, 'Groups': [group], 'AssociatePublicIpAddress': True}],
            BlockDeviceMappings=[{'DeviceName': '/dev/xvda', 'Ebs': {'VolumeSize': 40, 'VolumeType': 'gp3', 'Encrypted': True, 'DeleteOnTermination': True}}],
            TagSpecifications=[{'ResourceType': kind, 'Tags': tags} for kind in ['instance', 'volume']])['Instances'][0]
        state.update(client_instance=item['InstanceId'], client_started_at=datetime.now(timezone.utc).isoformat(), client_type=args.client_type)
        state['created'].append('client_instance'); save(folder/'resources.json', state)
        print(json.dumps({'server': state['instance'], 'client': state['client_instance'], 'bucket': state['bucket']})); return
    if args.action == 'download': invoke('download', folder); return
    if args.action == 'fetch':
        key = args.key
        assert key.startswith(('control/', 'evidence/')) and '..' not in key
        value = s3.get_object(Bucket=state['bucket'], Key=key)['Body'].read()
        path = folder/'fetched'/key; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(value)
        print(value.decode()); return
    instance_id = state['instance' if args.role == 'server' else 'client_instance']
    instance = ec2.describe_instances(InstanceIds=[instance_id])['Reservations'][0]['Instances'][0]
    assert {t['Key']: t['Value'] for t in instance['Tags']}.get('Name') == state['name']
    if args.action == 'remote':
        import re
        assert args.script and re.fullmatch(r'[a-zA-Z0-9_.-]+', args.script.name)
        key = 'bootstrap/commands/'+args.script.name
        s3.upload_file(str(args.script), state['bucket'], key)
        cmd = ssm.send_command(InstanceIds=[instance_id], DocumentName='AWS-RunShellScript', Parameters={'commands': [
            f'aws s3 cp s3://{state["bucket"]}/{key} /tmp/{args.script.name} --only-show-errors', f'bash /tmp/{args.script.name}'], 'executionTimeout': ['900']}, Comment='Owned mixed upload-search experiment')['Command']['CommandId']
        with (folder/'commands.jsonl').open('a') as stream: stream.write(json.dumps({'id': cmd, 'role': args.role, 'script': args.script.name, 'at': time.time()})+'\n')
        print(json.dumps({'command': cmd})); return
    result = ssm.get_command_invocation(CommandId=args.command, InstanceId=instance_id)
    output = result['StandardOutputContent']; error = result['StandardErrorContent']
    for value in json.loads((folder/'private.json').read_text()).values():
        output = output.replace(value, '[REDACTED]'); error = error.replace(value, '[REDACTED]')
    print(json.dumps({'status': result['Status'], 'stdout': output, 'stderr': error}))


if __name__ == '__main__': main()
