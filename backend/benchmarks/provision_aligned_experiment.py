"""Add the long fixture to the existing guarded two-host experiment provisioner."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

import provision_mixed_experiment as mixed


def prepare(folder):
    mixed.prepare(folder)
    source = mixed.base.ROOT/'.review-cache/aligned-experiments/fixtures/speech-900s.mp4'
    assert source.is_file() and 0 < source.stat().st_size < 500*1024**2
    old_path, new_path = folder/'bootstrap.tar.gz', folder/'aligned-bootstrap.tar.gz'
    with tarfile.open(old_path, 'r:gz') as old, tarfile.open(new_path, 'w:gz') as new:
        for member in old.getmembers():
            new.addfile(member, old.extractfile(member) if member.isfile() else None)
        new.add(source, arcname='fixtures/speech-900s.mp4')
    new_path.replace(old_path)
    hashes = json.loads((folder/'source-hashes.json').read_text())
    hashes['fixtures/speech-900s.mp4'] = hashlib.sha256(source.read_bytes()).hexdigest()
    mixed.save(folder/'source-hashes.json', hashes)
    state = json.loads((folder/'resources.json').read_text())
    state['campaign_kind'] = 'aligned-architecture'
    mixed.save(folder/'resources.json', state)
    fixtures = {}
    for name, path in [('speech-900s.mp4', source), ('speech-20s.mp4', mixed.base.ROOT/'.review-cache/pipeline-experiments/fixtures/speech-20s.mp4')]:
        fixtures[name] = {'bytes': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    mixed.save(folder/'fixtures.json', fixtures)
    print(json.dumps({'prepared': state['name'], 'archive_bytes': old_path.stat().st_size, 'fixtures': fixtures}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.output.resolve())
