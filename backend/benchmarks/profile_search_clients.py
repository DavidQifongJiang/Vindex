"""Measure client lifecycle only: no search requests or service mutations."""
import json
from pathlib import Path
import platform
from time import perf_counter
from unittest.mock import patch

import numpy as np
from qdrant_client import QdrantClient
from app.services.projection_indexes import ElasticsearchProjectionIndex


def main():
    output = Path('tmp/search-quality-v2/client-lifecycle.json')
    if output.exists():raise ValueError('Refusing to replace an existing measurement')
    rows = []
    # Disable compatibility checks and forbid network sends. This experiment
    # isolates constructor/close overhead from actual backend retrieval.
    with patch('httpx.Client.send', side_effect=AssertionError('Unexpected network I/O')), \
            patch('requests.Session.send', side_effect=AssertionError('Unexpected network I/O')):
        for n in range(21):
            for mode in (['qdrant', 'elasticsearch'] if n % 2 else ['elasticsearch', 'qdrant']):
                started = perf_counter()
                client = (QdrantClient(url='http://127.0.0.1:16336', timeout=2, check_compatibility=False)
                          if mode == 'qdrant' else ElasticsearchProjectionIndex('lifecycle-profile', url='http://127.0.0.1:19201'))
                constructed = perf_counter()
                client.close()
                ended = perf_counter()
                if n:
                    rows.append(dict(mode=mode, construct_ms=(constructed-started)*1000,
                                     close_ms=(ended-constructed)*1000, total_ms=(ended-started)*1000))
    result = dict(python=platform.python_version(), rounds=20, rows=rows,
        scope='Constructor and close only, network sends prohibited. Shared developer machine. Not end-to-end search or a measured pooling improvement.',
        summary={m:{f:dict(p50=float(np.percentile([r[f] for r in rows if r['mode']==m],50)),
                          p95=float(np.percentile([r[f] for r in rows if r['mode']==m],95)))
                    for f in ['construct_ms', 'close_ms', 'total_ms']} for m in ['qdrant','elasticsearch']})
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result['summary'], indent=2))


if __name__ == '__main__':main()
