# Vindex Pipeline Benchmark Stats

Benchmark target: `medium` video, `test_video.mp4`

- Duration: 1158.188 seconds, about 19.3 minutes
- Chunking strategy: 5-minute chunks with 5-second overlap
- Chunk count: 4 chunks
- Primary metric: time-to-searchable, from upload completion to searchable status

## Timeline

| Stage | Environment | Search workers | Time-to-searchable | Total processing | Notes |
|---|---:|---:|---:|---:|---|
| Before DAG | Local | 1 | 68.14s | 65.04s | Original monolithic/local baseline |
| DAG only | Local | 1 | 56.17s | 56.37s | Playback/search split, no chunked search |
| DAG only, warm run | Local | 1 | 47.65s | 47.84s | Best warm local DAG run |
| Chunked DAG | Local | 1 | 61.12s | 61.37s | 4 chunks, one local search worker |
| Chunked DAG, warm run | Local | 1 | 72.18s | 72.44s | Warm local chunked run used for comparison |
| Chunked DAG, scale test | Local | 2 | 105.58s | 105.84s | Local CPU contention appeared |
| Chunked DAG, scale test | Local | 2 | 121.50s | 121.77s | Second local two-worker run |
| Before DAG | EC2 `t3.medium` | 1 | 226.74s | 226.02s | Original online baseline |
| Chunked DAG | EC2 `t3.medium` | 1 | 172.97s | 214.04s | Search branch becomes ready before playback finishes |
| Chunked DAG, scale test | EC2 `t3.medium` | 2 | 216.41s | 220.50s | Worse because the 2-vCPU instance is CPU-constrained |
| Chunked DAG | EC2 `c7i.2xlarge` | 1 | 101.79s | 101.94s | 8-vCPU scaling experiment |
| Chunked DAG | EC2 `c7i.2xlarge` | 2 | 58.84s | 59.00s | Two chunks processed concurrently |
| Chunked DAG | EC2 `c7i.2xlarge` | 4 | 40.73s | 46.29s | Best tested point for a 4-chunk video |

## Improvement Summary

| Comparison | Time-to-searchable change |
|---|---:|
| Online before DAG -> online chunked DAG on `t3.medium`, 1 worker | 23.7% faster |
| Online chunked DAG on `t3.medium`, 1 worker -> `t3.medium`, 2 workers | 25.1% slower |
| Online chunked DAG on `t3.medium`, 1 worker -> `c7i.2xlarge`, 1 worker | 41.2% faster |
| `c7i.2xlarge`, 1 worker -> `c7i.2xlarge`, 2 workers | 42.2% faster |
| `c7i.2xlarge`, 1 worker -> `c7i.2xlarge`, 4 workers | 60.0% faster |
| Online before DAG -> `c7i.2xlarge`, 4 workers | 82.0% faster |
| Online chunked DAG on `t3.medium`, 1 worker -> `c7i.2xlarge`, 4 workers | 76.5% faster |

## Interpretation

The DAG split improved user-facing readiness because search can complete independently of playback transcoding.

Chunking did not help much on CPU-constrained machines by itself. It created parallel work, but a small machine could not execute that work efficiently. This is why `t3.medium` with 2 search workers became slower.

On the 8-vCPU `c7i.2xlarge`, the chunked design scaled as intended. The 20-minute video has 4 chunks, so 4 search workers is the maximum useful count for a single upload of this size. More workers would only help if multiple videos were being processed concurrently.

Best measured result:

```text
226.74s old online baseline -> 40.73s chunked DAG on c7i.2xlarge with 4 search workers
```

That is an 82.0% reduction in time-to-searchable.

