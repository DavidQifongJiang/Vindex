"""Create/status/destroy a short-lived, isolated AWS upload benchmark.

Uses a dedicated bucket, IAM role, security group and EC2 host. No production
service, database, bucket policy or task definition is modified. Resource IDs
are journaled immediately for recovery. Run with --help; creation is explicit.
"""
import argparse
from datetime import datetime, timezone
import ipaddress
import json
import re
from pathlib import Path
import secrets
import subprocess
import tarfile
import time
from urllib.request import urlopen
from uuid import uuid4

import boto3

ROOT = Path(__file__).resolve().parents[2]
IMAGE = '585572506964.dkr.ecr.us-east-1.amazonaws.com/vindex@sha256:ee08240512c0f2b5b0101261ba74e4bd0aeccaba71519592c788647d9d50dcbc'
COMMIT = 'a458bc2e5b19335025eed8ec63599e2c02297330'
VPC = 'vpc-03c4326eb68a52837'
SUBNET = 'subnet-045498468210035a9'
ACCOUNT = '585572506964'
REGION = 'us-east-1'


def validate_resource_journal(state):
    name = state.get('name', '')
    if state.get('account') != ACCOUNT or not re.fullmatch(r'vindex-upload-bench-[0-9a-f]{10}', name):
        raise ValueError('Invalid benchmark owner or name')
    bucket = f'vindex-upload-bench-{ACCOUNT}-' + name.removeprefix('vindex-upload-bench-')
    if state.get('bucket', bucket) != bucket or any(state.get(k, name) != name for k in ('role', 'profile')):
        raise ValueError('Resource names differ from run identity')
    if state.get('image', IMAGE) != IMAGE:
        raise ValueError('Image differs from pinned comparison source')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['create', 'status', 'destroy'])
    parser.add_argument('--account', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    session = boto3.Session(profile_name='default', region_name=REGION)
    if session.client('sts').get_caller_identity()['Account'] != args.account or args.account != ACCOUNT:
        raise SystemExit('Account mismatch; refusing cloud operations')
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    state_path = output / 'resources.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {}
    def persist():
        state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    ec2, iam, s3 = (session.client(service) for service in ('ec2', 'iam', 's3'))
    if args.action == 'create':
        if state:
            raise SystemExit('Output already has a resource journal; use status/destroy or a new output')
        for mode in ['api', 'direct']:
            if not (ROOT / '.review-cache/upload-comparison' / f'frontend-{mode}/index.html').is_file():
                raise SystemExit('Build both benchmark frontends before provisioning resources')
        suffix = uuid4().hex[:10]
        name = f'vindex-upload-bench-{suffix}'
        bucket = f'vindex-upload-bench-{ACCOUNT}-{suffix}'
        state.update(name=name, account=ACCOUNT, region=REGION, source_commit=COMMIT,
                     created_at=datetime.now(timezone.utc).isoformat(), image=IMAGE,
                     instance_type='t3.large', maximum_host_hours=4)
        persist()
        tags = [{'Key': 'Project', 'Value': 'vindex-upload-benchmark'}, {'Key': 'Run', 'Value': suffix}]
        ip = str(ipaddress.ip_address(urlopen('https://checkip.amazonaws.com', timeout=20).read().decode().strip()))
        state['client_ip'] = ip
        s3.create_bucket(Bucket=bucket)
        state['bucket'] = bucket; persist()
        s3.put_public_access_block(Bucket=bucket, PublicAccessBlockConfiguration={
            k: True for k in ['BlockPublicAcls', 'IgnorePublicAcls', 'BlockPublicPolicy', 'RestrictPublicBuckets']})
        s3.put_bucket_tagging(Bucket=bucket, Tagging={'TagSet': tags})
        s3.put_bucket_lifecycle_configuration(Bucket=bucket, LifecycleConfiguration={'Rules': [
            {'ID': 'expire-temporary-data', 'Status': 'Enabled', 'Filter': {'Prefix': ''},
             'Expiration': {'Days': 2}, 'AbortIncompleteMultipartUpload': {'DaysAfterInitiation': 1}}]})
        role = iam.create_role(RoleName=name, Tags=tags, AssumeRolePolicyDocument=json.dumps({
            'Version': '2012-10-17', 'Statement': [{'Effect': 'Allow', 'Principal': {'Service': 'ec2.amazonaws.com'}, 'Action': 'sts:AssumeRole'}]}))['Role']
        state['role'] = role['RoleName']; persist()
        iam.put_role_policy(RoleName=name, PolicyName='benchmark-only', PolicyDocument=json.dumps({
            'Version': '2012-10-17', 'Statement': [
                {'Effect': 'Allow', 'Action': ['s3:ListBucket'], 'Resource': f'arn:aws:s3:::{bucket}'},
                {'Effect': 'Allow', 'Action': ['s3:GetObject', 's3:PutObject', 's3:DeleteObject', 's3:AbortMultipartUpload'], 'Resource': f'arn:aws:s3:::{bucket}/*'},
                {'Effect': 'Allow', 'Action': 'ecr:GetAuthorizationToken', 'Resource': '*'},
                {'Effect': 'Allow', 'Action': ['ecr:BatchCheckLayerAvailability', 'ecr:GetDownloadUrlForLayer', 'ecr:BatchGetImage'], 'Resource': f'arn:aws:ecr:{REGION}:{ACCOUNT}:repository/vindex'}]}))
        iam.attach_role_policy(RoleName=name, PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
        iam.create_instance_profile(InstanceProfileName=name, Tags=tags)
        state['profile'] = name; persist()
        iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=name)
        group = ec2.create_security_group(GroupName=name, Description='Temporary upload benchmark; one client only', VpcId=VPC,
            TagSpecifications=[{'ResourceType': 'security-group', 'Tags': tags}])['GroupId']
        state['security_group'] = group; persist()
        ec2.authorize_security_group_ingress(GroupId=group, IpPermissions=[{'IpProtocol': 'tcp', 'FromPort': 8443, 'ToPort': 8443,
            'IpRanges': [{'CidrIp': ip + '/32', 'Description': 'Benchmark browser client'}]}])
        private = {'token': secrets.token_urlsafe(40), 'database_password': secrets.token_urlsafe(32)}
        (output / 'private.json').write_text(json.dumps(private), encoding='utf-8')
        env = '\n'.join([f'DATABASE_URL=postgresql+psycopg2://bench:{private["database_password"]}@bench-postgres:5432/vindex_upload_bench',
            'STORAGE_BACKEND=s3', f'S3_BUCKET_NAME={bucket}', 'AWS_REGION=us-east-1', 'AWS_DEFAULT_REGION=us-east-1',
            'AUTH_REQUIRED=false', 'DEV_USER_ID=upload-benchmark', 'RUN_MIGRATIONS_ON_STARTUP=false',
            'VINDEX_BENCHMARK_ISOLATED=true', f'VINDEX_BENCH_TOKEN={private["token"]}', f'VINDEX_BENCH_COMMIT={COMMIT}',
            'HF_HUB_OFFLINE=1', 'TRANSFORMERS_OFFLINE=1', 'PYTHONPATH=/bench:/app', 'MAX_UPLOAD_MB=500', 'DIRECT_UPLOAD_REQUIRE_CHECKSUM=false']) + '\n'
        (output / 'app.env').write_text(env, encoding='utf-8')
        (output / 'postgres.env').write_text(f'POSTGRES_USER=bench\nPOSTGRES_PASSWORD={private["database_password"]}\nPOSTGRES_DB=vindex_upload_bench\n', encoding='utf-8')
        startup = f'''#!/bin/bash
set -euo pipefail
cd /opt/upload-bench
chmod 600 app.env postgres.env
mkdir -p tls
openssl req -x509 -newkey rsa:2048 -keyout tls/key.pem -out tls/cert.pem -sha256 -days 2 -nodes -subj /CN=upload-benchmark >/dev/null 2>&1
docker network create upload-bench
aws ecr get-login-password --region {REGION} | docker login --username AWS --password-stdin {ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com
docker pull {IMAGE}
docker run -d --name bench-postgres --network upload-bench --cpus=0.5 --memory=768m --env-file postgres.env postgres:16
for i in $(seq 1 60); do docker exec bench-postgres pg_isready -U bench -d vindex_upload_bench && break; sleep 2; done
docker run --rm --network upload-bench --env-file app.env {IMAGE} alembic upgrade head
docker run -d --name bench-api --network upload-bench --cpus=0.5 --memory=2g --memory-swap=2g -p 8443:8443 --env-file app.env -v /opt/upload-bench:/bench:ro {IMAGE} uvicorn upload_bench_server:application --host 0.0.0.0 --port 8443 --ssl-keyfile /bench/tls/key.pem --ssl-certfile /bench/tls/cert.pem --no-access-log
docker image inspect {IMAGE} --format '{{{{.Id}}}}' > image-id.txt
echo benchmark-ready
'''
        (output / 'start.sh').write_text(startup, encoding='utf-8', newline='\n')
        archive = output / 'bootstrap.tar.gz'
        with tarfile.open(archive, 'w:gz') as tar:
            for name_ in ['app.env', 'postgres.env', 'start.sh']:
                tar.add(output / name_, arcname=name_)
            tar.add(Path(__file__).with_name('upload_bench_server.py'), arcname='upload_bench_server.py')
            for mode in ['api', 'direct']:
                tar.add(ROOT / '.review-cache' / 'upload-comparison' / f'frontend-{mode}', arcname=f'frontend-{mode}')
        s3.upload_file(str(archive), bucket, 'bootstrap/bootstrap.tar.gz')
        user_data = f'''#!/bin/bash
set -euo pipefail
systemd-run --unit=upload-bench-expiry --on-active=4h /usr/sbin/poweroff
dnf install -y docker
systemctl enable --now docker
mkdir -p /opt/upload-bench
aws s3 cp s3://{bucket}/bootstrap/bootstrap.tar.gz /opt/upload-bench/bootstrap.tar.gz --region {REGION}
tar -xzf /opt/upload-bench/bootstrap.tar.gz -C /opt/upload-bench
bash /opt/upload-bench/start.sh
'''
        ami = session.client('ssm').get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
        time.sleep(12)  # IAM instance-profile propagation.
        launched = ec2.run_instances(ImageId=ami, InstanceType='t3.large', MinCount=1, MaxCount=1,
            IamInstanceProfile={'Name': state['profile']}, UserData=user_data,
            InstanceInitiatedShutdownBehavior='terminate',
            CreditSpecification={'CpuCredits': 'standard'},
            MetadataOptions={'HttpTokens': 'required', 'HttpPutResponseHopLimit': 2},
            NetworkInterfaces=[{'DeviceIndex': 0, 'SubnetId': SUBNET, 'Groups': [group], 'AssociatePublicIpAddress': True}],
            BlockDeviceMappings=[{'DeviceName': '/dev/xvda', 'Ebs': {'VolumeSize': 35, 'VolumeType': 'gp3', 'DeleteOnTermination': True, 'Encrypted': True}}],
            TagSpecifications=[{'ResourceType': t, 'Tags': tags + [{'Key': 'Name', 'Value': state['name']}]} for t in ['instance', 'volume']])['Instances'][0]
        state['instance'] = launched['InstanceId']; state['ami'] = ami; persist()
        print(json.dumps({'created': state['name'], 'instance': state['instance'], 'bucket': bucket}), flush=True)
        # EC2 IDs are eventually consistent immediately after RunInstances.
        ec2.get_waiter('instance_exists').wait(InstanceIds=[state['instance']], WaiterConfig={'Delay': 3, 'MaxAttempts': 15})
    if args.action in ['create', 'status'] and state.get('instance'):
        instance = ec2.describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
        ip = instance.get('PublicIpAddress')
        if ip and state.get('origin') != f'https://{ip}:8443':
            state['origin'] = f'https://{ip}:8443'; persist()
            s3.put_bucket_cors(Bucket=state['bucket'], CORSConfiguration={'CORSRules': [{'AllowedOrigins': [state['origin']],
                'AllowedMethods': ['POST', 'GET', 'HEAD'], 'AllowedHeaders': ['*'], 'ExposeHeaders': ['ETag'], 'MaxAgeSeconds': 300}]})
        print(json.dumps({'state': instance['State']['Name'], 'origin': state.get('origin'), 'instance': state['instance']}))
    if args.action == 'destroy':
        validate_resource_journal(state)
        expected_bucket = f'vindex-upload-bench-{ACCOUNT}-' + state['name'].removeprefix('vindex-upload-bench-')
        if state.get('bucket', expected_bucket) != expected_bucket or state.get('role', state['name']) != state['name'] or state.get('profile', state['name']) != state['name']:
            raise SystemExit('Resource names do not match benchmark journal')
        cleaned = state.setdefault('cleaned', [])
        def mark(name):
            cleaned.append(name)
            persist()
        if state.get('instance') and 'instance' not in cleaned:
            instance = ec2.describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
            if {t['Key']: t['Value'] for t in instance.get('Tags', [])}.get('Name') != state['name']:
                raise SystemExit('Instance ownership tags do not match journal')
            ec2.terminate_instances(InstanceIds=[state['instance']])
            ec2.get_waiter('instance_terminated').wait(InstanceIds=[state['instance']], WaiterConfig={'Delay': 5, 'MaxAttempts': 36})
            mark('instance')
        if state.get('bucket') and 'bucket' not in cleaned:
            tags = s3.get_bucket_tagging(Bucket=state['bucket'])['TagSet']
            if {t['Key']: t['Value'] for t in tags}.get('Project') != 'vindex-upload-benchmark':
                raise SystemExit('Bucket ownership tags do not match')
            for page in s3.get_paginator('list_objects_v2').paginate(Bucket=state['bucket']):
                keys = [{'Key': o['Key']} for o in page.get('Contents', [])]
                if keys:
                    result = s3.delete_objects(Bucket=state['bucket'], Delete={'Objects': keys}, ExpectedBucketOwner=ACCOUNT)
                    if result.get('Errors'):
                        raise RuntimeError('Bucket cleanup incomplete')
            s3.delete_bucket(Bucket=state['bucket'], ExpectedBucketOwner=ACCOUNT)
            mark('bucket')
        if state.get('security_group') and 'security_group' not in cleaned:
            group = ec2.describe_security_groups(GroupIds=[state['security_group']])['SecurityGroups'][0]
            if group['GroupName'] != state['name'] or group['VpcId'] != VPC:
                raise SystemExit('Security group ownership does not match journal')
            ec2.delete_security_group(GroupId=state['security_group'])
            mark('security_group')
        if state.get('profile') and 'profile' not in cleaned:
            iam.remove_role_from_instance_profile(InstanceProfileName=state['profile'], RoleName=state['role'])
            iam.delete_instance_profile(InstanceProfileName=state['profile'])
            mark('profile')
        if state.get('role') and 'role' not in cleaned:
            iam.detach_role_policy(RoleName=state['role'], PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
            iam.delete_role_policy(RoleName=state['role'], PolicyName='benchmark-only')
            iam.delete_role(RoleName=state['role'])
            mark('role')
        state['destroyed_at'] = datetime.now(timezone.utc).isoformat(); persist()
        print(json.dumps({'destroyed': state['name']}))


if __name__ == '__main__':
    main()
