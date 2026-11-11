"""Journaled, opt-in isolated pipeline host. No shared Vindex services are changed."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import secrets
import shutil
import tarfile
import time
from uuid import uuid4

import boto3
from botocore.exceptions import ClientError

ROOT=Path(__file__).resolve().parents[2]
ACCOUNT='585572506964'
REGION='us-east-1'
VPC='vpc-03c4326eb68a52837'
SUBNET='subnet-045498468210035a9'
COMMIT='a458bc2e5b19335025eed8ec63599e2c02297330'
IMAGES={
    'APP_IMAGE':f'{ACCOUNT}.dkr.ecr.{REGION}.amazonaws.com/vindex@sha256:ee08240512c0f2b5b0101261ba74e4bd0aeccaba71519592c788647d9d50dcbc',
    'POSTGRES_IMAGE':'postgres@sha256:5db6dac2ef5dcff512f4f00a009c3c332b9f2f28f24277175bf3e4a3d02ef83a',
    'REDIS_IMAGE':'redis@sha256:b6b6e3dc0c0952737748e55d5d885272840c7e42ba41ab1d5489f693419882f5',
    'QDRANT_IMAGE':'qdrant/qdrant@sha256:9bfc89f9914fc14876a88c05270685921a55b8877b254a98bcc1cc2f0a424cd2',
    'PROXY_IMAGE':'caddy@sha256:ce773ec7a742ce15ee6bf2bb5f65882b9e2a4487b10733e6f1484f86e6b5d8bc',
    'BROWSER_IMAGE':'mcr.microsoft.com/playwright@sha256:2f05f8a734509a7b3f26a912639488ee08f58a7b292d14d62271bcff9038768f'}


def validate_state(state):
    if state.get('account')!=ACCOUNT or not re.fullmatch(r'vindex-pipeline-bench-[a-f0-9]{10}',state.get('name','')):
        raise ValueError('Invalid experiment identity')
    if state.get('bucket') != f'vindex-pipeline-bench-{ACCOUNT}-'+state['name'].split('-')[-1]:
        raise ValueError('Bucket does not match experiment identity')
    if state.get('image') != IMAGES['APP_IMAGE']: raise ValueError('Wrong measured image')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','create','status','download','destroy'])
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--account',required=True)
    args=parser.parse_args()
    folder=args.output.resolve(); folder.mkdir(parents=True,exist_ok=True)
    state_path=folder/'resources.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {}
    def persist(): state_path.write_text(json.dumps(state,indent=2))
    if args.account!=ACCOUNT: raise SystemExit('Wrong account')
    if args.action=='prepare':
        if state: raise SystemExit('Existing resource journal; do not overwrite')
        suffix=uuid4().hex[:10]; name='vindex-pipeline-bench-'+suffix
        state={'account':ACCOUNT,'name':name,'bucket':f'vindex-pipeline-bench-{ACCOUNT}-{suffix}',
               'image':IMAGES['APP_IMAGE'],'commit':COMMIT,'instance_type':'m7i.2xlarge','hourly_compute_usd':0.4032,
               'budget_usd':5,'initial_lifetime_minutes':60,'prepared_at':datetime.now(timezone.utc).isoformat(),'created':[]}
        persist()
        private={'token':secrets.token_urlsafe(40),'database_password':secrets.token_urlsafe(32)}
        (folder/'private.json').write_text(json.dumps(private))
        env={'DATABASE_URL':f'postgresql+psycopg2://bench:{private["database_password"]}@bench-postgres:5432/vindex_pipeline_bench',
             'STORAGE_BACKEND':'s3','S3_BUCKET_NAME':state['bucket'],'AWS_REGION':REGION,'AWS_DEFAULT_REGION':REGION,
             'AUTH_REQUIRED':'false','DEV_USER_ID':'pipeline-benchmark','RUN_MIGRATIONS_ON_STARTUP':'false',
             'VINDEX_PIPELINE_EXPERIMENT':'true','VINDEX_BENCH_TOKEN':private['token'],
             'PYTHONPATH':'/bench:/app','CELERY_BROKER_URL':'redis://bench-redis:6379/0',
             'CELERY_RESULT_BACKEND':'redis://bench-redis:6379/0','REDIS_URL':'redis://bench-redis:6379/0',
             'QDRANT_URL':'http://bench-qdrant:6333','WHISPER_MODEL':'tiny','EMBEDDING_MODEL':'all-MiniLM-L6-v2',
             'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','TOKENIZERS_PARALLELISM':'false',
             'OMP_NUM_THREADS':'1','MKL_NUM_THREADS':'1','OPENBLAS_NUM_THREADS':'1','NUMEXPR_NUM_THREADS':'1',
             'MAX_UPLOAD_MB':'500','DIRECT_UPLOAD_REQUIRE_CHECKSUM':'false'}
        (folder/'app.env').write_text(''.join(f'{k}={v}\n' for k,v in env.items()),newline='\n')
        (folder/'postgres.env').write_text(f'POSTGRES_USER=bench\nPOSTGRES_PASSWORD={private["database_password"]}\nPOSTGRES_DB=vindex_pipeline_bench\n',newline='\n')
        (folder/'runtime.env').write_text(''.join(f'{k}={v}\n' for k,v in IMAGES.items()),newline='\n')
        (folder/'run-settings.json').write_text(json.dumps({'bucket':state['bucket'],'image':state['image'],'commit':COMMIT},indent=2))
        files={}
        archive=folder/'bootstrap.tar.gz'
        with tarfile.open(archive,'w:gz') as tar:
            for name in ['private.json','app.env','postgres.env','runtime.env','run-settings.json']: tar.add(folder/name,arcname=name)
            for source in (Path(__file__).parent/'pipeline_experiment').iterdir():
                if source.is_file():
                    tar.add(source,arcname=source.name); files[source.name]=hashlib.sha256(source.read_bytes()).hexdigest()
            for name in ['run_upload_comparison.cjs','upload_measurement.cjs','pipeline_measurement.cjs','pipeline_browser_probes.cjs']:
                source=Path(__file__).with_name(name); tar.add(source,arcname='backend/benchmarks/'+name); files[name]=hashlib.sha256(source.read_bytes()).hexdigest()
            for mode in ['api','direct']: tar.add(ROOT/'.review-cache/upload-comparison'/f'frontend-{mode}',arcname=f'frontend-{mode}')
            for name in ['playwright','playwright-core']: tar.add(ROOT/'frontend/node_modules'/name,arcname='frontend/node_modules/'+name)
            fixture=ROOT/'.review-cache/pipeline-experiments/fixtures/speech-20s.mp4'
            tar.add(fixture,arcname='fixtures/speech-20s.mp4')
            files['speech-20s.mp4']=hashlib.sha256(fixture.read_bytes()).hexdigest()
        (folder/'source-hashes.json').write_text(json.dumps(files,indent=2))
        print(json.dumps({'prepared':state['name'],'archive_bytes':archive.stat().st_size})); return
    validate_state(state)
    session=boto3.Session(profile_name='default',region_name=REGION)
    if session.client('sts').get_caller_identity()['Account']!=ACCOUNT: raise SystemExit('Wrong AWS identity')
    ec2,iam,s3=session.client('ec2'),session.client('iam'),session.client('s3')
    def mark(item): state['created'].append(item); persist()
    if args.action=='create':
        if state['created']: raise SystemExit('Partially or fully created; inspect before resuming')
        name=state['name']; bucket=state['bucket']; tags=[{'Key':'Project','Value':'vindex-pipeline-experiment'},{'Key':'Name','Value':name}]
        s3.create_bucket(Bucket=bucket); mark('bucket')
        s3.put_public_access_block(Bucket=bucket,PublicAccessBlockConfiguration={k:True for k in ['BlockPublicAcls','IgnorePublicAcls','BlockPublicPolicy','RestrictPublicBuckets']})
        s3.put_bucket_tagging(Bucket=bucket,Tagging={'TagSet':tags})
        s3.put_bucket_lifecycle_configuration(Bucket=bucket,LifecycleConfiguration={'Rules':[{'ID':'expire-test-data','Status':'Enabled','Filter':{'Prefix':''},'Expiration':{'Days':7}}]})
        s3.put_bucket_cors(Bucket=bucket,CORSConfiguration={'CORSRules':[{'AllowedOrigins':['https://bench-proxy:8443'],'AllowedMethods':['GET','POST','HEAD'],'AllowedHeaders':['*'],'ExposeHeaders':['ETag'],'MaxAgeSeconds':300}]})
        iam.create_role(RoleName=name,Tags=tags,AssumeRolePolicyDocument=json.dumps({'Version':'2012-10-17','Statement':[{'Effect':'Allow','Principal':{'Service':'ec2.amazonaws.com'},'Action':'sts:AssumeRole'}]})); mark('role')
        iam.put_role_policy(RoleName=name,PolicyName='experiment-only',PolicyDocument=json.dumps({'Version':'2012-10-17','Statement':[
            {'Effect':'Allow','Action':['s3:ListBucket'],'Resource':f'arn:aws:s3:::{bucket}'},
            {'Effect':'Allow','Action':['s3:GetObject','s3:PutObject','s3:DeleteObject'],'Resource':f'arn:aws:s3:::{bucket}/*'},
            {'Effect':'Allow','Action':'ecr:GetAuthorizationToken','Resource':'*'},
            {'Effect':'Allow','Action':['ecr:BatchCheckLayerAvailability','ecr:GetDownloadUrlForLayer','ecr:BatchGetImage'],'Resource':f'arn:aws:ecr:{REGION}:{ACCOUNT}:repository/vindex'}]}))
        iam.attach_role_policy(RoleName=name,PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
        iam.create_instance_profile(InstanceProfileName=name,Tags=tags); mark('profile')
        iam.add_role_to_instance_profile(InstanceProfileName=name,RoleName=name)
        state['security_group']=ec2.create_security_group(GroupName=name,Description='Isolated pipeline experiment; no inbound access',VpcId=VPC,
            TagSpecifications=[{'ResourceType':'security-group','Tags':tags}])['GroupId']; mark('security_group')
        s3.upload_file(str(folder/'bootstrap.tar.gz'),bucket,'bootstrap/bootstrap.tar.gz')
        user_data=f'''#!/bin/bash
set -euo pipefail
systemd-run --unit=pipeline-expiry --on-active=60m /bin/bash -c 'systemctl stop pipeline-campaign.service; docker stop -t 5 bench-browser bench-dispatcher bench-search bench-playback >/dev/null 2>&1; cd /opt/vindex-experiment; timeout 120 /usr/bin/python3.11 campaign.py --snapshot automatic-expiry; /usr/sbin/poweroff'
dnf install -y docker python3.11
systemctl enable --now docker
mkdir -p /opt/vindex-experiment
aws s3 cp s3://{bucket}/bootstrap/bootstrap.tar.gz /opt/vindex-experiment/bootstrap.tar.gz --region {REGION} --only-show-errors
tar -xzf /opt/vindex-experiment/bootstrap.tar.gz -C /opt/vindex-experiment
bash /opt/vindex-experiment/bootstrap.sh
'''
        ami=session.client('ssm').get_parameter(Name='/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64')['Parameter']['Value']
        time.sleep(12)
        instance=ec2.run_instances(ImageId=ami,InstanceType=state['instance_type'],MinCount=1,MaxCount=1,IamInstanceProfile={'Name':name},UserData=user_data,
            InstanceInitiatedShutdownBehavior='stop',MetadataOptions={'HttpTokens':'required','HttpPutResponseHopLimit':2},
            NetworkInterfaces=[{'DeviceIndex':0,'SubnetId':SUBNET,'Groups':[state['security_group']],'AssociatePublicIpAddress':True}],
            BlockDeviceMappings=[{'DeviceName':'/dev/xvda','Ebs':{'VolumeSize':60,'VolumeType':'gp3','Encrypted':True,'DeleteOnTermination':True}}],
            TagSpecifications=[{'ResourceType':kind,'Tags':tags} for kind in ['instance','volume']])['Instances'][0]
        state.update(instance=instance['InstanceId'],volume=instance['BlockDeviceMappings'][0]['Ebs']['VolumeId'] if instance.get('BlockDeviceMappings') else None,
                     started_at=datetime.now(timezone.utc).isoformat()); mark('instance')
        print(json.dumps({'instance':state['instance'],'bucket':bucket})); return
    if args.action=='status':
        instance=ec2.describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
        state['volume']=instance['BlockDeviceMappings'][0]['Ebs']['VolumeId']; persist()
        print(json.dumps({'state':instance['State']['Name'],'instance':state['instance'],'volume':state['volume']})); return
    if args.action=='download':
        dest=folder/'evidence'; dest.mkdir(exist_ok=True)
        files={}
        try:
            marker=json.loads(s3.get_object(Bucket=state['bucket'],Key='evidence/latest-archive.json')['Body'].read())
        except ClientError as exc:
            if exc.response['Error']['Code']!='NoSuchKey': raise
            marker=None
        if marker:
            assert re.fullmatch(r'evidence/[a-zA-Z0-9_-]+-evidence.tar.gz',marker['key'])
            packed=folder/Path(marker['key']).name
            s3.download_file(state['bucket'],marker['key'],str(packed))
            assert packed.stat().st_size==marker['bytes'] and hashlib.sha256(packed.read_bytes()).hexdigest()==marker['sha256']
            with tarfile.open(packed,'r:gz') as tar:
                for member in tar.getmembers():
                    target=(dest/member.name).resolve()
                    if not target.is_relative_to(dest.resolve()) or not member.isfile(): raise ValueError('Unsafe archive member')
                    target.parent.mkdir(parents=True,exist_ok=True)
                    with tar.extractfile(member) as source, target.open('wb') as stream: shutil.copyfileobj(source,stream)
            manifest=json.loads((dest/f'archive-manifest-{marker["label"]}.json').read_text())
            assert manifest['bucket']==state['bucket']
            for relative,item in manifest['files'].items():
                target=(dest/relative).resolve()
                assert target.is_relative_to(dest.resolve()) and target.stat().st_size==item['bytes']
                assert hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256']
            (folder/'downloaded-evidence.json').write_text(json.dumps({'bucket':state['bucket'],'downloaded_at':datetime.now(timezone.utc).isoformat(),
                'files':manifest['files'],'archive':marker},indent=2))
            print(json.dumps({'downloaded_files':len(manifest['files']),'archive_verified':True})); return
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=state['bucket'],Prefix='evidence/'):
            for item in page.get('Contents',[]):
                relative=Path(item['Key'].removeprefix('evidence/'))
                target=(dest/relative).resolve()
                if not target.is_relative_to(dest.resolve()): raise ValueError('Unsafe archive path')
                target.parent.mkdir(parents=True,exist_ok=True)
                s3.download_file(state['bucket'],item['Key'],str(target))
                files[str(relative).replace('\\','/')]={'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
        if not files: raise RuntimeError('No archived evidence')
        write={'bucket':state['bucket'],'downloaded_at':datetime.now(timezone.utc).isoformat(),'files':files}
        (folder/'downloaded-evidence.json').write_text(json.dumps(write,indent=2))
        print(json.dumps({'downloaded_files':len(files)})); return
    if args.action=='destroy':
        proof=json.loads((folder/'downloaded-evidence.json').read_text())
        assert proof['bucket']==state['bucket'] and proof['files']
        for relative,item in proof['files'].items():
            target=(folder/'evidence'/relative).resolve()
            assert target.is_relative_to((folder/'evidence').resolve())
            assert hashlib.sha256(target.read_bytes()).hexdigest()==item['sha256']
        # A separate reconciliation receipt is required, even for failed trials.
        receipt=json.loads((folder/'cleanup-ready.json').read_text())
        assert receipt['bucket']==state['bucket'] and receipt['workers_stopped'] is True and receipt['all_objects_preserved_or_verified'] is True
        instance=ec2.describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
        assert {t['Key']:t['Value'] for t in instance['Tags']}.get('Name')==state['name']
        assert instance['State']['Name']=='stopped', 'Stop the experiment host and preserve its final snapshot first'
        assert {t['Key']:t['Value'] for t in s3.get_bucket_tagging(Bucket=state['bucket'])['TagSet']}.get('Name')==state['name']
        # Prove any residual media survived locally before terminating the database host.
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=state['bucket']):
            for obj in page.get('Contents',[]):
                if obj['Key'].startswith(('bootstrap/','evidence/')): continue
                preserved=receipt.get('preserved_objects',{}).get(obj['Key'])
                assert preserved and preserved['bytes']==obj['Size'] and preserved['etag']==obj['ETag'], 'Unpreserved residual object'
                local=(folder/'preserved'/obj['Key']).resolve()
                assert local.is_relative_to((folder/'preserved').resolve())
                assert hashlib.sha256(local.read_bytes()).hexdigest()==preserved['sha256']
        ec2.terminate_instances(InstanceIds=[state['instance']])
        ec2.get_waiter('instance_terminated').wait(InstanceIds=[state['instance']],WaiterConfig={'Delay':5,'MaxAttempts':40})
        for page in s3.get_paginator('list_objects_v2').paginate(Bucket=state['bucket']):
            keys=[{'Key':o['Key']} for o in page.get('Contents',[])]
            if keys: assert not s3.delete_objects(Bucket=state['bucket'],Delete={'Objects':keys},ExpectedBucketOwner=ACCOUNT).get('Errors')
        s3.delete_bucket(Bucket=state['bucket'],ExpectedBucketOwner=ACCOUNT)
        group=ec2.describe_security_groups(GroupIds=[state['security_group']])['SecurityGroups'][0]
        assert group['GroupName']==state['name'] and group['VpcId']==VPC
        ec2.delete_security_group(GroupId=state['security_group'])
        iam.remove_role_from_instance_profile(InstanceProfileName=state['name'],RoleName=state['name'])
        iam.delete_instance_profile(InstanceProfileName=state['name'])
        iam.detach_role_policy(RoleName=state['name'],PolicyArn='arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore')
        iam.delete_role_policy(RoleName=state['name'],PolicyName='experiment-only'); iam.delete_role(RoleName=state['name'])
        state['destroyed_at']=datetime.now(timezone.utc).isoformat(); persist()
        print(json.dumps({'destroyed':state['name']}))


if __name__=='__main__': main()
