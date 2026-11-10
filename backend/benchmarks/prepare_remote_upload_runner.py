"""Run the browser campaign on dedicated AWS compute and archive checkpoints.

The runner has no inbound ports. It reuses only the dedicated benchmark role,
bucket and VPC; production is never a target. Both hosts expire after four hours.
"""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

import boto3

if __package__:
    from .provision_upload_bench import validate_resource_journal, ACCOUNT, REGION, VPC, SUBNET, ROOT, COMMIT
else:
    from provision_upload_bench import validate_resource_journal, ACCOUNT, REGION, VPC, SUBNET, ROOT, COMMIT

BROWSER_IMAGE = 'mcr.microsoft.com/playwright@sha256:2f05f8a734509a7b3f26a912639488ee08f58a7b292d14d62271bcff9038768f'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources', type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.resources.read_text())
    validate_resource_journal(state)
    if state.get('runner_instance'):
        raise SystemExit('Runner already journaled; inspect it instead of creating duplicates')
    s = boto3.Session(profile_name='default', region_name=REGION)
    if s.client('sts').get_caller_identity()['Account'] != ACCOUNT:
        raise SystemExit('Wrong AWS account')
    ec2, s3 = s.client('ec2'), s.client('s3')
    api = ec2.describe_instances(InstanceIds=[state['instance']])['Reservations'][0]['Instances'][0]
    if {t['Key']:t['Value'] for t in api['Tags']}.get('Name') != state['name']:
        raise SystemExit('API host ownership mismatch')
    folder = args.resources.resolve().parent / 'runner'
    folder.mkdir()
    private = json.loads(args.resources.with_name('private.json').read_text())
    (folder/'private.json').write_text(json.dumps({'token':private['token']}))
    origin = f'https://{api["PrivateIpAddress"]}:8443'
    s3.put_bucket_cors(Bucket=state['bucket'], CORSConfiguration={'CORSRules':[{
        'AllowedOrigins':[origin,state['origin']], 'AllowedMethods':['POST','GET','HEAD'],
        'AllowedHeaders':['*'], 'ExposeHeaders':['ETag'], 'MaxAgeSeconds':300}]})
    cells=[]
    for name, file, clients, repetitions in [('small-c5','video-25mib.mp4',5,20),
            ('medium-c5','video-100mib.mp4',5,20), ('small-c1','video-25mib.mp4',1,100)]:
        manifest={'environment':'staging','commit':COMMIT,'targets':{mode:origin+'/bench-'+mode+'/' for mode in ['api','direct']},
            'files':['/opt/upload-client/fixtures/'+file], 'repetitions':repetitions,'concurrency':clients,
            'timeout_seconds':600,'stop_after_failed_wave':True,
            'network':{'name':'20mbps-40ms','latency_ms':40,'upload_mbps':20,'download_mbps':100},
            'benchmark_control':{'origin':origin,'secret_file':'/opt/upload-client/private.json'}}
        (folder/(name+'.json')).write_text(json.dumps(manifest,indent=2))
        cells.append({'name':name,'manifest':'/opt/upload-client/'+name+'.json'})
    (folder/'campaign-plan.json').write_text(json.dumps({'cells':cells},indent=2))
    # The pilot is a separate campaign, excluded from final statistics.
    pilot={**manifest,'concurrency':1,'repetitions':2}
    (folder/'pilot.json').write_text(json.dumps(pilot,indent=2))
    (folder/'pilot-plan.json').write_text(json.dumps({'cells':[{'name':'pilot','manifest':'/opt/upload-client/pilot.json'}]}))
    source_files=['run_upload_campaign.py','run_upload_comparison.cjs','upload_measurement.cjs',
                  'pipeline_measurement.cjs','pipeline_browser_probes.cjs']
    sources={f:hashlib.sha256(Path(__file__).with_name(f).read_bytes()).hexdigest() for f in source_files}
    (folder/'source-manifest.json').write_text(json.dumps({'sources':sources,'browser_image':BROWSER_IMAGE,
        'client_type':'c7i.xlarge','source_commit':COMMIT,'node_version':'22.23.4','api_https':'separate_proxy'},indent=2))
    wait_script='''import json,ssl,time
from pathlib import Path
from urllib.request import Request,urlopen
p=json.loads(Path('/opt/upload-client/private.json').read_text())
m=json.loads(Path('/opt/upload-client/pilot.json').read_text())
for attempt in range(90):
    try:
        req=Request(m['benchmark_control']['origin']+'/bench/control/info',headers={'Cookie':'vindex_bench='+p['token']})
        with urlopen(req,context=ssl._create_unverified_context(),timeout=5) as r:
            info=json.loads(r.read())
        if info.get('https_termination')=='separate_proxy':
            Path('/opt/upload-client/results/server-ready.json').write_text(json.dumps(info,indent=2));break
    except Exception: pass
    time.sleep(5)
else: raise SystemExit('Isolated proxy API not ready')
'''
    (folder/'wait-ready.py').write_text(wait_script,encoding='utf-8',newline='\n')
    archive=f'''#!/bin/bash
set -eu
while systemctl is-active --quiet upload-bench-campaign.service; do
  aws s3 sync /opt/upload-client/results s3://{state['bucket']}/evidence/remote-001/ --region us-east-1 --only-show-errors
  sleep 30
done
aws s3 sync /opt/upload-client/results s3://{state['bucket']}/evidence/remote-001/ --region us-east-1 --only-show-errors
python3.11 - <<'PY'
import json,hashlib
from pathlib import Path
root=Path('/opt/upload-client/results')
files={{str(p.relative_to(root)):{{'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}} for p in root.rglob('*') if p.is_file() and p.name!='archive-complete.json'}}
(root/'archive-complete.json').write_text(json.dumps({{'files':files}},indent=2))
PY
aws s3 cp /opt/upload-client/results/archive-complete.json s3://{state['bucket']}/evidence/remote-001/archive-complete.json --region us-east-1 --only-show-errors
'''
    (folder/'archive.sh').write_text(archive,encoding='utf-8',newline='\n')
    start=f'''#!/bin/bash
set -euo pipefail
cd /opt/upload-client
mkdir -p results fixtures
cp source-manifest.json results/source-manifest.json
for file in video-25mib.mp4 video-100mib.mp4; do aws s3 cp s3://{state['bucket']}/fixtures/$file fixtures/$file --region us-east-1 --only-show-errors; done
docker pull {BROWSER_IMAGE}
docker run -d --init --name bench-runner --shm-size=1g --cpus=4 --memory=6g --memory-swap=6g -v /opt/upload-client:/opt/upload-client -w /opt/upload-client {BROWSER_IMAGE} sleep infinity
docker exec bench-runner npm install --no-audit --no-fund --prefix /opt/upload-client/runtime node@22.23.4
cat > /opt/upload-client/node22 <<'NODE'
#!/bin/bash
exec docker exec bench-runner /opt/upload-client/runtime/node_modules/node/bin/node "$@"
NODE
chmod 700 /opt/upload-client/node22
python3.11 wait-ready.py
cat > /opt/upload-client/run.sh <<'RUN'
#!/bin/bash
set -euo pipefail
cd /opt/upload-client
python3.11 backend/benchmarks/run_upload_campaign.py --manifest pilot-plan.json --output results/pilot --node /opt/upload-client/node22
python3.11 backend/benchmarks/run_upload_campaign.py --manifest campaign-plan.json --output results/primary --node /opt/upload-client/node22
RUN
chmod 700 run.sh archive.sh
cat > /etc/systemd/system/upload-bench-campaign.service <<'UNIT'
[Unit]
Description=Isolated upload comparison campaign
After=docker.service
[Service]
Type=simple
ExecStart=/opt/upload-client/run.sh
StandardOutput=append:/opt/upload-client/results/console.log
StandardError=append:/opt/upload-client/results/console.log
UNIT
cat > /etc/systemd/system/upload-bench-archive.service <<'UNIT'
[Unit]
Description=Archive benchmark checkpoints
After=upload-bench-campaign.service
[Service]
Type=simple
ExecStart=/bin/bash /opt/upload-client/archive.sh
UNIT
systemctl daemon-reload
systemctl start upload-bench-campaign.service upload-bench-archive.service
echo remote-campaign-started
'''
    (folder/'start.sh').write_text(start,encoding='utf-8',newline='\n')
    with tarfile.open(folder/'runner.tar.gz','w:gz') as tar:
        for file in folder.iterdir():
            if file.name != 'runner.tar.gz':tar.add(file,arcname=file.name)
        for name in source_files:
            tar.add(Path(__file__).with_name(name),arcname='backend/benchmarks/'+name)
        for name in ['playwright','playwright-core']:
            tar.add(ROOT/'frontend/node_modules'/name,arcname='frontend/node_modules/'+name)
    s3.upload_file(str(folder/'runner.tar.gz'),state['bucket'],'bootstrap/runner.tar.gz')
    for name in ['video-25mib.mp4','video-100mib.mp4']:
        s3.upload_file(str(ROOT/'.review-cache/upload-comparison/fixtures'/name),state['bucket'],'fixtures/'+name)
    tags=[{'Key':'Project','Value':'vindex-upload-benchmark'},{'Key':'Name','Value':state['name']+'-runner'}]
    group=ec2.create_security_group(GroupName=state['name']+'-runner',Description='No inbound access; isolated benchmark client',VpcId=VPC,
        TagSpecifications=[{'ResourceType':'security-group','Tags':tags}])['GroupId']
    state['runner_security_group']=group
    def persist():args.resources.write_text(json.dumps(state,indent=2))
    persist()
    ec2.authorize_security_group_ingress(GroupId=state['security_group'],IpPermissions=[{'IpProtocol':'tcp','FromPort':8443,'ToPort':8443,'UserIdGroupPairs':[{'GroupId':group}]}])
    user_data=f'''#!/bin/bash
set -euo pipefail
systemd-run --unit=upload-client-expiry --on-active=4h /usr/sbin/poweroff
dnf install -y docker python3.11
systemctl enable --now docker
mkdir -p /opt/upload-client
aws s3 cp s3://{state['bucket']}/bootstrap/runner.tar.gz /opt/upload-client/runner.tar.gz --region us-east-1
tar -xzf /opt/upload-client/runner.tar.gz -C /opt/upload-client
bash /opt/upload-client/start.sh
'''
    launched=ec2.run_instances(ImageId=state['ami'],InstanceType='c7i.xlarge',MinCount=1,MaxCount=1,
        IamInstanceProfile={'Name':state['profile']},UserData=user_data,InstanceInitiatedShutdownBehavior='terminate',
        MetadataOptions={'HttpTokens':'required','HttpPutResponseHopLimit':2},
        NetworkInterfaces=[{'DeviceIndex':0,'SubnetId':SUBNET,'Groups':[group],'AssociatePublicIpAddress':True}],
        BlockDeviceMappings=[{'DeviceName':'/dev/xvda','Ebs':{'VolumeSize':35,'VolumeType':'gp3','DeleteOnTermination':True,'Encrypted':True}}],
        TagSpecifications=[{'ResourceType':t,'Tags':tags} for t in ['instance','volume']])['Instances'][0]
    state['runner_instance']=launched['InstanceId'];state['runner_type']='c7i.xlarge';state['runner_origin']=origin;state['archive_prefix']='evidence/remote-001/';persist()
    print(json.dumps({'runner_instance':state['runner_instance'],'origin':origin,'archive_prefix':state['archive_prefix']}))


if __name__=='__main__':main()
