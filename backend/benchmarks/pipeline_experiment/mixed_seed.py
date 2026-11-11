"""Create the fixed processed corpus before timing any mixed-load trials."""
import json
import os
import requests
from guard import validate_environment

validate_environment(os.environ)
ids = []
for index in range(3):
    with open('/bench/fixtures/speech-20s.mp4', 'rb') as stream:
        response = requests.post('http://127.0.0.1:8000/videos/upload',
            cookies={'vindex_bench': os.environ['VINDEX_BENCH_TOKEN']},
            files={'file': ('speech-20s.mp4', stream, 'video/mp4')},
            data={'title': 'Protected mixed-load seed '+str(index), 'visibility': 'private'}, timeout=180)
    assert response.status_code == 200, 'Seed admission failed'
    ids.append(response.json()['video_id'])
print(json.dumps(ids))
