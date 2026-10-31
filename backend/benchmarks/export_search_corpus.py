"""Export complete authorized segments from a historical benchmark config."""
import argparse
import json
import os
from pathlib import Path
import requests


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--api-base-url', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--token-env', default='VINDEX_BENCHMARK_TOKEN')
    args = parser.parse_args()
    config = json.loads(args.config.read_text(encoding='utf-8'))
    headers = {'Authorization': 'Bearer ' + os.environ[args.token_env]} if os.getenv(args.token_env) else {}
    videos = []
    with requests.Session() as session:
        session.headers.update(headers)
        for original in config['videos']:
            base = args.api_base_url.rstrip('/') + '/videos/' + original['video_id']
            def get(path):
                response = session.get(base + path, timeout=60)
                response.raise_for_status()
                return response.json()
            before = get('/status')
            segments = get('/segments')['segments']
            after = get('/status')
            if before['segments_path'] != after['segments_path'] or after['status'] == 'deleting':
                raise RuntimeError('Video changed during export; retry')
            videos.append({'video_key': original['video_key'], 'video_id': original['video_id'],
                           'revision': after['segments_path'], 'segments': segments})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'videos': videos}, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
