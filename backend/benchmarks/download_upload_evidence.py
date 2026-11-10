"""Download checkpointed reports; verify the final hash manifest before teardown."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath

import boto3

if __package__:
    from .provision_upload_bench import validate_resource_journal, ACCOUNT, REGION
else:
    from provision_upload_bench import validate_resource_journal, ACCOUNT, REGION


def evidence_path(root, name):
    relative = PurePosixPath(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name or not relative.parts:
        raise ValueError('Unsafe evidence path')
    resolved = root.joinpath(*relative.parts).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('Evidence path escapes output')
    return resolved


def verify_manifest(root, manifest):
    if not manifest.get('files'):
        raise ValueError('Empty evidence manifest')
    for name, expected in manifest['files'].items():
        path = evidence_path(root, name)
        if path.stat().st_size != expected['bytes'] or hashlib.sha256(path.read_bytes()).hexdigest() != expected['sha256']:
            raise ValueError('Archived evidence checksum mismatch: '+name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--resources',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--require-complete',action='store_true')
    args = parser.parse_args()
    state=json.loads(args.resources.read_text());validate_resource_journal(state)
    s=boto3.Session(profile_name='default',region_name=REGION)
    if s.client('sts').get_caller_identity()['Account']!=ACCOUNT:
        raise SystemExit('Account mismatch')
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True)
    marker=root/'download-state.json'
    identity={'bucket':state['bucket'],'prefix':state['archive_prefix']}
    if marker.exists() and any(json.loads(marker.read_text()).get(k)!=v for k,v in identity.items()):
        raise SystemExit('Output belongs to a different run')
    if not marker.exists() and any(root.iterdir()):
        raise SystemExit('Refusing to overwrite an existing directory without run identity')
    marker.write_text(json.dumps(identity,indent=2))
    client=s.client('s3');downloaded=0
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=state['bucket'],Prefix=state['archive_prefix'],ExpectedBucketOwner=ACCOUNT):
        for item in page.get('Contents',[]):
            name=item['Key'].removeprefix(state['archive_prefix']);path=evidence_path(root,name)
            path.parent.mkdir(parents=True,exist_ok=True)
            client.download_file(state['bucket'],item['Key'],str(path));downloaded+=1
    summary={**identity,'downloaded_files':downloaded,'checked_at':datetime.now(timezone.utc).isoformat(),'complete':False}
    if (root/'archive-complete.json').exists():
        verify_manifest(root,json.loads((root/'archive-complete.json').read_text()))
        summary['archive_hashes_verified']=True
        campaign_path=root/'primary/campaign.json'
        campaign=json.loads(campaign_path.read_text()) if campaign_path.exists() else {}
        summary['complete']=bool(campaign.get('completed'))
        if summary['complete']:
            for cell in campaign['cells']:
                completion=json.loads(evidence_path(root,'primary/'+cell['name']+'/completion.json').read_text())
                if not completion['finished_all_planned_waves'] or completion['recorded_attempts']!=completion['planned_attempts']:
                    raise ValueError('Primary cell incomplete')
        args.resources.with_name('evidence-verified.json').write_text(json.dumps({**summary,'local_directory':str(root)},indent=2))
    marker.write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))
    if args.require_complete and not summary['complete']:
        raise SystemExit('Campaign incomplete; checkpoint preserved')


if __name__=='__main__':main()
