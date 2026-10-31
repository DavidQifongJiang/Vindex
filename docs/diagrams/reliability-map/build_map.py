"""Build the Whimsical sources and source index for Vindex reliability diagrams.

No application services, credentials, production data, or benchmarks are used.
Run from any directory with Python 3.11+; render_map.cjs produces the previews.
"""
import json
import re
from pathlib import Path
from textwrap import dedent

ROOT = Path(__file__).resolve().parent
PANELS = []
STYLES = '''
classDef normal fill:#edf4ff,stroke:#4b83bb,color:#172f4a
classDef decision fill:#fff5dc,stroke:#b88d27,color:#4c3810
classDef safe fill:#eaf7f0,stroke:#40916b,color:#193c2b
classDef retry fill:#fff0de,stroke:#ce873a,color:#513211
classDef fail fill:#ffeded,stroke:#c76a6a,color:#542525
classDef note fill:#f1f3f6,stroke:#97a2ad,color:#344353
classDef optional fill:#f2edfc,stroke:#8e74bc,color:#35254d
'''


def add(slug, title, scope, sources, tests, notes, code):
    # Native SVG text labels in Mermaid can double-escape ASCII comparisons.
    # Use readable Unicode symbols only inside labels; preserve edge syntax.
    def clean_label(match):
        label = match.group(0).replace('<br/>', '\x00')
        label = label.replace('->', '→').replace('>=', '≥').replace('<=', '≤')
        label = label.replace('>', ' greater than ').replace('<', ' less than ')
        return label.replace('\x00', '<br/>')
    code = re.sub(r'"[^"\n]*"', clean_label, dedent(code).strip())
    PANELS.append(dict(slug=slug, title=title, scope=scope, sources=sources.split(),
                       tests=tests.split(), notes=notes, code=code))


add('00-overview', '00 · Reliability overview', 'Core and optional paths',
    'app/db/upload_repository.py app/db/pipeline_repository.py app/db/search_repository.py app/db/search_event_repository.py',
    'tests/test_durable_pipeline.py tests/test_operational_safety.py tests/test_search_indexing.py tests/test_search_events.py',
    'Solid arrows describe the main lifecycle. Numbered references lead to detailed panels. Postgres is the authority; Redis and Kafka carry messages. No exactly-once execution or cross-system rollback is claimed.', '''
    O1["01 Upload intent<br/>Owner + idempotency key + fingerprint"]:::normal
    O2["02 Atomic transaction<br/>Upload response + video + durable jobs"]:::normal
    O3["02 Dispatcher -> Redis<br/>Republish unclaimed work"]:::normal
    O4["03 Claim + lease + deadline<br/>Postgres permits one current attempt"]:::normal
    O5["06 Search jobs<br/>Prepare -> chunks -> finalize"]:::normal
    O6["06 Playback job<br/>Transcode -> optional thumbnail"]:::normal
    O7["06 Guarded publication<br/>Accepted paths and tokens in Postgres"]:::safe
    O8["Video processed<br/>Search complete AND playback published"]:::safe
    O9["04-05 Errors and retries<br/>Classify -> bounded retry or terminal failure"]:::retry
    O10["07 Crash recovery<br/>08 Deletion, garbage collection and repair"]:::note
    O11["09-10 OPTIONAL library search<br/>Projection leases, integrity checks and fail-closed reads"]:::optional
    O12["11-13 OPTIONAL Kafka prototype<br/>Capture, outbox, receipts, quarantine and replay"]:::optional
    O13["14-16 Supporting controls<br/>Metrics, alerts, backups and legacy limits"]:::note
    O1 --> O2 --> O3 --> O4
    O4 --> O5 --> O7
    O4 --> O6 --> O7 --> O8
    O4 -.-> O9
    O9 -->|Retry due| O3
    O9 -.-> O10
    O7 -.-> O11 -.-> O12
    O10 -.-> O13
''')

add('01-upload-idempotency', '01 · Upload idempotency', 'Core',
    'frontend/src/api.ts app/api/videos.py app/db/upload_repository.py app/db/video_repository.py',
    'tests/test_operational_safety.py',
    'The frontend retains a UUID for the same File object, title and visibility; a reload or new file selection is a new intent. No automatic upload retry loop is implemented. Without a supplied key the server generates a new key for that request. This is request deduplication, not global content deduplication.', '''
    U1["Frontend retains Idempotency-Key<br/>WeakMap: File + title + visibility"]:::normal
    U2["Authenticate; normalize metadata<br/>Stream file SHA-256; rewind file<br/>Fingerprint includes filename, title, visibility, size"]:::normal
    U3{"Size and key valid?"}:::decision
    U4["413 above MAX_UPLOAD_MB: default 500<br/>400 invalid key: allowed A-Z a-z 0-9 . _ : -<br/>Key length: 1-128"]:::fail
    U5["Owner + key is unique identity<br/>Insert with savepoint; tolerate insert race<br/>SELECT FOR UPDATE reservation"]:::normal
    U6{"Fingerprint matches?"}:::decision
    U7["409 changed content<br/>Do not reuse this key for another payload"]:::fail
    U8{"Already completed?"}:::decision
    U9{"Video exists and is not deleting?"}:::decision
    U10["Replay stored response<br/>No S3 upload; no duplicate jobs"]:::safe
    U11["410 deleted upload<br/>Retain reservation tombstone; use new key"]:::fail
    U12{"Unexpired reservation?"}:::decision
    U13["409 in progress<br/>Retry-After: 5; retry SAME key"]:::retry
    U14["Reserve or take over expired intent<br/>Keep video ID; new UUID attempt token<br/>New raw S3 key; expiry default 1800s"]:::normal
    U15["Commit reservation; release DB transaction<br/>Upload to raw_videos/video/token/file"]:::normal
    U16{"Current token AND unexpired?"}:::decision
    U17["ONE transaction<br/>Response + completed reservation + video<br/>Search job + playback job"]:::safe
    U18["409 expired attempt<br/>Cannot replace the newer upload owner"]:::fail
    U19["S3 or transaction failure<br/>Rollback and expire matching uploading token<br/>DB unavailable: log and rely on expiry"]:::retry
    U20["Lost response after commit<br/>Retry SAME key -> replay stored response"]:::safe
    U21["Unreferenced uploaded object<br/>Collected after grace period: panel 08"]:::note
    U1 --> U2 --> U3
    U3 -->|No| U4
    U3 -->|Yes| U5 --> U6
    U6 -->|No| U7
    U6 -->|Yes| U8
    U8 -->|Yes| U9
    U9 -->|Yes| U10
    U9 -->|No| U11
    U8 -->|No| U12
    U12 -->|Yes| U13
    U12 -->|No| U14 --> U15 --> U16
    U16 -->|Yes| U17 --> U20
    U16 -->|No| U18 --> U21
    U15 -.->|Failure| U19
    U17 -.->|Failure| U19
    U19 --> U21
''')

add('02-durable-dag-dispatch', '02 · Durable dependencies and dispatch', 'Core',
    'app/db/video_repository.py app/db/pipeline_repository.py app/workers/pipeline_dispatcher.py app/workers/celery_app.py app/db/models.py',
    'tests/test_durable_pipeline.py',
    'Redis delivery is at least once. Dispatch reservation does not increment the processing attempt count; a successful database claim does. The dispatcher is required for new uploads. Search, chunk and finalizer tasks use the search queue; playback uses playback; optional projections use indexing.', '''
    D1["Upload commit creates durable intent<br/>pipeline_jobs: unique video + stage<br/>search preparation AND playback"]:::normal
    D2["Preparation completion transaction<br/>Accepted audio paths + all chunk rows<br/>video_search_chunks: unique video + chunk index"]:::safe
    D3["Each chunk completes independently<br/>Commit accepted artifact token<br/>Keep successful chunks when another retries"]:::safe
    D4["Last completed chunk creates finalize job<br/>Same transaction; parent lock + unique stage<br/>One finalizer, independent attempt budget"]:::safe
    D5["Dispatcher iteration<br/>Recover expired/exhausted work first<br/>Then dispatch; default every 5s, minimum 1s"]:::normal
    D6["Read up to 100 candidates per model<br/>pending OR retrying with retry time due<br/>Attempts remain; dispatch window due; oldest first"]:::normal
    D7["Release scan transaction; lock parent video<br/>Missing/deleting video -> skip<br/>Recheck status and dispatch window"]:::normal
    D8["Commit dispatch_after = now + 60s<br/>Minimum configured redispatch interval 10s<br/>Reserve BEFORE calling broker"]:::normal
    D9["Publish Celery task to Redis<br/>send_task with retry=False<br/>Task carries video ID and optional chunk index"]:::normal
    D10{"Worker claims before next window?"}:::decision
    D11["Processing row is no longer dispatchable<br/>Worker lifecycle: panel 03"]:::safe
    D12["No claim; Redis loss; ambiguous send<br/>Dispatcher crash before/after send<br/>Durable row remains eligible after window"]:::retry
    D13["Publish exception ends this dispatch batch<br/>Log; next iteration can recover leases<br/>Record successful dispatcher operation state"]:::note
    D14["Redis/Celery transport controls<br/>Late ACK; reject on worker loss<br/>ACK failure/timeouts; prefetch multiplier 1"]:::note
    D15["Transport defaults<br/>Visibility timeout 7200s<br/>Broker connect/socket timeouts 5s"]:::note
    D1 --> D2 --> D3 --> D4
    D1 -.-> D5
    D2 -.-> D5
    D4 -.-> D5
    D5 --> D6 --> D7 --> D8 --> D9 --> D10
    D10 -->|Yes| D11
    D10 -->|No| D12 --> D5
    D9 -.->|Exception| D13
    D9 -.-> D14 --> D15
''')

add('03-claim-lease-deadline', '03 · Claims, leases and deadlines', 'Core',
    'app/db/pipeline_repository.py app/workers/durable_task.py app/services/processing_lease.py app/services/media_service.py app/workers/celery_app.py',
    'tests/test_durable_pipeline.py tests/test_operational_safety.py',
    'No database transaction stays open across media/model work. Lease renewal and publication require both a live lease and a live absolute deadline. Ordinary failure recording checks the matching token and a live lease; expired-work recovery has a separate guarded path. Terminal video status alone does not cancel already pending sibling work.', '''
    L1["Celery message arrives<br/>Owner = hostname, task ID, or unknown-worker"]:::normal
    L2["Lock parent video<br/>Missing or deleting -> no claim"]:::normal
    L3{"pending OR due retrying<br/>AND persisted attempts below max?"}:::decision
    L4["Return skipped<br/>Duplicate, premature retry, active/completed/<br/>failed work, missing/deleting parent"]:::safe
    L5["Atomic claim; commit and detach row<br/>status processing; attempt count +1<br/>New UUID token; owner; started/progress timestamps"]:::normal
    L6["Lease = now + 300s; minimum 30s<br/>Absolute deadline = now + 1800s<br/>Clear next_retry_at; deadline is not renewable"]:::normal
    L7["Run processor outside claim transaction<br/>Daemon heartbeat every lease/3: default 100s<br/>Explicit checkpoints also update progress_at"]:::normal
    L8{"Parent available; status processing;<br/>same token; lease and deadline live?"}:::decision
    L9["Renew lease and commit<br/>Checkpoint records progress<br/>Never extend the absolute deadline"]:::safe
    L10["Stale heartbeat -> lost flag<br/>Next checkpoint raises StaleProcessingAttempt<br/>Wrapper returns stale; no result publication"]:::fail
    L11["Other heartbeat exception -> log<br/>May continue computing<br/>Cannot publish without a successful DB guard"]:::retry
    L12["Additional limits<br/>FFmpeg/ffprobe command timeout 900s<br/>Celery soft 1800s; hard 1830s"]:::note
    L13["Soft timeout -> error classifier<br/>Hard kill/crash -> lease/deadline recovery<br/>OS time limits require supported Linux prefork"]:::note
    L14["Claim DB failure<br/>No processor starts; wrapper exits with error<br/>Pending work can be redispatched"]:::retry
    L1 --> L2 --> L3
    L3 -->|No| L4
    L3 -->|Yes| L5 --> L6 --> L7 --> L8
    L8 -->|Yes| L9 --> L7
    L8 -->|No| L10
    L7 -.->|DB or network exception| L11 --> L8
    L7 -.-> L12 --> L13
    L2 -.->|DB unavailable| L14
''')

add('04-error-classification', '04 · Error classification', 'Core and reused by projection workers',
    'app/services/processing_errors.py app/services/video_service.py app/services/storage_service.py',
    'tests/test_operational_safety.py tests/test_video_repository_retry_state.py',
    'Classification determines whether another attempt is allowed; panel 05 also checks the persisted budget. Dotted links between error boxes group alternative cases rather than showing an execution sequence. Existing ProcessingError subclasses retain their classification. Wrapped S3 transfers are classified using their last exception/cause/context when available. Unknown exceptions are terminal rather than retried indefinitely.', '''
    C1["Processor raises an exception"]:::normal
    C2{"StaleProcessingAttempt?"}:::decision
    C3["Return stale<br/>No failure transition for replacement owner"]:::safe
    C4["Existing ProcessingError -> preserve<br/>Wrapped S3 transfer -> unwrap cause<br/>Unclassified S3 transfer -> temporary"]:::normal
    C5["TEMPORARY: time and resource limits<br/>TimeoutExpired; SoftTimeLimitExceeded; TimeoutError<br/>MemoryError; ENOSPC / ENOMEM / EAGAIN; model OOM"]:::retry
    C6["TEMPORARY: dependencies<br/>SQLAlchemy OperationalError / DisconnectionError<br/>S3 connect/read/endpoint/connection-closed errors"]:::retry
    C7["TEMPORARY: HTTP and throttling<br/>408, 429, 500, 502, 503, 504<br/>RequestTimeout, SlowDown, Throttling,<br/>ThrottlingException, TooManyRequestsException"]:::retry
    C8["TEMPORARY: vector/network/model clients<br/>Qdrant ResponseHandlingException<br/>httpx connect/timeout; requests connect/timeout<br/>Model HTTP errors with listed temporary status"]:::retry
    C9["TEMPORARY: FFmpeg resource failure<br/>Exit -9 or 137; stderr contains no space,<br/>cannot allocate memory, resource unavailable"]:::retry
    C10["PERMANENT: S3 required object missing<br/>404 / NoSuchKey / NotFound<br/>Other non-retryable S3 or Qdrant response"]:::fail
    C11["PERMANENT: invalid media or source<br/>Other FFmpeg CalledProcessError<br/>Unsupported/corrupt media; invalid duration;<br/>missing video; invalid chunk manifest"]:::fail
    C12["PERMANENT: unexpected exception<br/>Default unexpected_processing_error<br/>Stage wrappers provide specific error codes"]:::fail
    C13["Retryable flag + persisted attempt count<br/>Continue to panel 05"]:::normal
    C14["SPECIAL CASE: thumbnail generation<br/>Exception is caught; playback continues without thumbnail<br/>Thumbnail UPLOAD failure is classified normally"]:::note
    C1 --> C2
    C2 -->|Yes| C3
    C2 -->|No| C4
    C4 --> C5
    C5 -.->|Other temporary cases| C6
    C6 -.-> C7
    C7 -.-> C8
    C8 -.-> C9
    C4 --> C10
    C10 -.->|Other permanent cases| C11
    C11 -.-> C12
    C9 --> C13
    C12 --> C13
    C1 -.-> C14
''')

add('05-retry-and-recovery', '05 · Retry state and expired-work recovery', 'Core',
    'app/db/pipeline_repository.py app/services/retry_policy.py app/workers/pipeline_dispatcher.py app/workers/durable_task.py',
    'tests/test_durable_pipeline.py tests/test_operational_safety.py',
    'Three attempts means three total successful claims, not three extra retries. A fresh Celery message with request.retries == 0 does not reset this budget. Retry is a Postgres state transition; durable tasks do not call Celery task.retry. Recovery latency includes detection, polling, backoff, and queue delay.', '''
    R1["Worker exception -> classify<br/>Lock parent; match processing status + token<br/>Ordinary failure recording requires live lease"]:::normal
    R2["Dispatcher scans expired lease OR deadline<br/>Also repairs pending/retrying rows already exhausted<br/>Re-lock parent; re-read before changing state"]:::normal
    R3["Recovery uses matching token + expired condition<br/>worker_lease_expired OR processing_deadline_exceeded<br/>Synthetic expired token for exhausted runnable rows"]:::normal
    R4{"Still owns eligible work?"}:::decision
    R5["No transition / stale<br/>Do not overwrite a replacement attempt"]:::safe
    R6{"Retryable AND attempts below max?"}:::decision
    R7["Default max attempts = 3, persisted per row<br/>Delay = min 600, 30 x 2^(attempt-1) seconds<br/>THEN random +/-20%, rounded, floor at zero"]:::retry
    R8["Default retry waits: 24-36s then 48-72s<br/>Cap is before jitter; up to 720s if reached<br/>Commit retrying + next_retry_at"]:::retry
    R9["Clear owner, token, lease and dispatch_after<br/>Persist code, class, message truncated to 1000,<br/>error time and updated_at"]:::normal
    R10["Wrapper returns retrying<br/>Dispatcher waits until due and republishes<br/>Next successful claim increments attempt"]:::retry
    R11["Commit work status = failed<br/>No automatic further attempt; mark video failed<br/>Search/chunk/finalize also mark search substates failed"]:::fail
    R12["Wrapper raises terminal classified error<br/>Completed sibling chunks remain usable<br/>Pending siblings are not automatically cancelled"]:::note
    R13["DB unavailable while recording failure<br/>No reliable state transition; error escapes<br/>After DB recovers, expiry path recovers work"]:::retry
    R1 --> R4
    R2 --> R3 --> R4
    R4 -->|No| R5
    R4 -->|Yes| R6
    R6 -->|Yes| R7 --> R8 --> R9 --> R10
    R6 -->|No| R11 --> R12
    R1 -.->|DB failure| R13 --> R2
    R10 -.->|Another attempt fails| R1
''')

add('06-publication-and-stages', '06 · Stage idempotency and guarded publication', 'Core',
    'app/services/video_service.py app/db/pipeline_repository.py app/services/qdrant_service.py app/db/video_repository.py',
    'tests/test_durable_pipeline.py tests/test_fenced_vectors.py',
    'Fencing protects accepted metadata and read visibility, not every external write. S3, Qdrant and Postgres do not share a transaction. Each new attempt has isolated outputs; repeating a write within one attempt uses deterministic keys/IDs. A stale attempt can leave garbage for panel 08.', '''
    P1["Preparation job<br/>If already searchable -> guarded completion<br/>If chunks already exist -> reuse, do not recreate"]:::normal
    P2["Extract audio to attempt-prefixed S3 keys<br/>Guard + commit chunk rows and preparation completion<br/>Completed chunks survive other failures"]:::safe
    P3["Chunk job: read audio -> Whisper -> embeddings<br/>Checkpoint before external publication<br/>Qdrant upsert wait=True + S3 manifests"]:::normal
    P4["Deterministic Qdrant ID<br/>UUID5 video + attempt token + segment ID<br/>Payload: attempt_token, chunk_index, write timestamp"]:::normal
    P5["S3 keys include attempt token<br/>Audio, chunk transcript/segments/embeddings,<br/>final manifests, processed video and thumbnail"]:::normal
    P6{"Final DB guard succeeds?<br/>Parent available; processing; matching token;<br/>unexpired lease AND deadline"}:::decision
    P7["Commit accepted paths + completed state<br/>Chunk artifact_token = current attempt token<br/>Clear active ownership + errors; schedule dependencies"]:::safe
    P8["Stale attempt cannot publish<br/>Earlier external writes remain unaccepted<br/>No distributed rollback"]:::fail
    P9["Finalizer validates complete chunk manifest<br/>Read ONLY accepted chunks; merge and sort<br/>Write attempt-specific final outputs"]:::normal
    P10["Finalizer transaction<br/>Publish final paths + search completed<br/>Optional revision + projection intent in same commit"]:::safe
    P11["Playback job<br/>Already published -> guarded completion<br/>Transcode; thumbnail generation is best effort"]:::normal
    P12["Upload processed video; upload thumbnail if created<br/>Upload errors use normal retry policy<br/>Guard and commit accepted playback paths"]:::normal
    P13["In completion transaction<br/>Search embeddings complete AND processed_path present<br/>Set overall video processed; avoid readiness crash gap"]:::safe
    P14["Search visibility fence<br/>Filter vectors by completed chunk artifact tokens<br/>Legacy tokenless vectors only for eligible legacy indexes<br/>No accepted tokens -> no results"]:::safe
    P16["Core collection creation race<br/>Qdrant create 409 is tolerated only if collection now exists<br/>Other creation errors propagate to classification"]:::note
    P1 --> P2 --> P3
    P3 --> P4 --> P6
    P3 --> P5 --> P6
    P6 -->|Yes| P7 --> P9 --> P10 --> P13
    P6 -->|No| P8
    P11 --> P12 --> P13
    P7 --> P14
    P4 -.-> P16
    P9 -.->|Failure before acceptance| P15["Use retry policy in panel 05<br/>Retry finalization from accepted chunks<br/>No retranscription"]:::retry
''')

add('07-crash-windows', '07 · Failure windows and recovery paths', 'Core; scenarios are independent',
    'app/db/upload_repository.py app/workers/pipeline_dispatcher.py app/workers/durable_task.py app/db/pipeline_repository.py app/services/processing_lease.py',
    'tests/test_durable_pipeline.py tests/test_operational_safety.py tests/lease_crash_worker.py',
    'These are independent recovery scenarios, not an execution chain or claims of production validation. Dotted connectors organize related cases vertically. No live service or benchmark is started to generate this board.', '''
    X0["Independent crash windows<br/>Each box pairs a failure with its recovery"]:::note
    X1["UPLOAD AND DELIVERY"]:::normal
    X2["RUNNING WORK"]:::normal
    X3["COMPLETION AND CLEANUP"]:::normal
    X4["Lost response after upload commit<br/>Same owner/key/fingerprint replays response<br/>No second video or initial jobs"]:::safe
    X5["S3 upload succeeded; DB commit did not<br/>Release reservation or await expiry<br/>New token on retry; orphan collected later"]:::retry
    X6["Dispatcher crash / Redis loss / ambiguous send<br/>Postgres intent survives<br/>Redispatch after reserved window"]:::retry
    X7["Duplicate or old queued message<br/>Atomic claim checks eligible state<br/>Active/completed/failed/missing work skips"]:::safe
    X8["Worker dies / hard timeout / lost heartbeat<br/>Lease or deadline expiry -> bounded retry<br/>Exhausted attempts -> terminal failure"]:::retry
    X9["DB fails during heartbeat or failure recording<br/>No unguarded acceptance<br/>Expiry recovery after database returns"]:::retry
    X10["Old worker returns after replacement starts<br/>Old token cannot renew or publish<br/>Late artifacts remain hidden and collectible"]:::safe
    X11["Finalizer fails after chunks completed<br/>Retry from accepted chunks; no retranscription<br/>Independent finalizer attempt budget"]:::retry
    X12["Artifacts written; crash before DB completion<br/>Outputs remain unaccepted<br/>New attempt isolated; old artifacts later collected"]:::retry
    X13["DB completion committed; crash before ACK<br/>Redelivery cannot claim completed work<br/>Accepted output is retained"]:::safe
    X14["Deletion cleanup interrupted<br/>Keep deleting state; owner retries endpoint<br/>GC catches abandoned and late artifacts"]:::retry
    X0 --> X1
    X0 --> X2
    X0 --> X3
    X1 -.-> X4 -.-> X5 -.-> X6 -.-> X7
    X2 -.-> X8 -.-> X9 -.-> X10 -.-> X11
    X3 -.-> X12 -.-> X13 -.-> X14
''')

add('08-delete-gc-repair', '08 · Deletion, orphan cleanup and vector repair', 'Core',
    'app/api/videos.py app/services/maintenance_service.py app/services/storage_service.py app/workers/maintenance.py',
    'tests/test_operational_safety.py tests/test_durable_pipeline.py',
    'Deletion is not a distributed transaction. Some raw orphan paths are collected by maintenance rather than the endpoint. Unknown path formats are intentionally retained. Core Qdrant repair detects missing counts; it does not verify every vector value, restore missing S3 objects, or restart terminally failed processing.', '''
    G1["Owner requests delete<br/>404 missing; enforce owner; lock video"]:::normal
    G2{"Active video status OR<br/>any pending / processing / retrying job or chunk?"}:::decision
    G3["409 until work finishes or fails<br/>Even failed parent cannot delete active siblings"]:::fail
    G4["Commit deleting BEFORE external cleanup<br/>Revoke search projections; deletion tombstone<br/>Future claims/publication reject unavailable parent"]:::normal
    G5["Delete Elasticsearch if configured<br/>Failure -> 503 retry deletion<br/>Delete Qdrant vectors and known S3 paths/prefixes"]:::normal
    G6["S3 batched deletes check per-object errors<br/>Any cleanup exception leaves deleting state<br/>Owner can repeat endpoint"]:::retry
    G7["After cleanup: remove chunks, jobs and video<br/>Upload tombstone remains for old key -> 410"]:::safe
    G8["Maintenance every 60s by default, minimum 10s<br/>Persist bounded scan cursors in operation_states<br/>S3/Qdrant pages 200; index repair 10 videos"]:::normal
    G9["Artifact collection grace default 86400s<br/>Minimum configured grace 3600s<br/>Leave recent objects and unknown paths alone"]:::note
    G10{"Protected by database references?"}:::decision
    G11["Keep accepted video/chunk paths<br/>Keep live upload raw path and active attempt tokens<br/>Keep accepted chunk vectors and restricted legacy data"]:::safe
    G12["Delete old unreferenced objects and vectors<br/>Includes writes from stale attempts after deletion<br/>Continue via persisted cursor"]:::normal
    G13["Repair accepted searchable videos<br/>Count Qdrant points for accepted token/chunk<br/>Missing count -> reload S3 embedding manifest"]:::normal
    G14["Upsert deterministic accepted vector IDs<br/>Record per-video error; continue other videos<br/>Wrap scan cursor rather than starve later rows"]:::safe
    G1 --> G2
    G2 -->|Yes| G3
    G2 -->|No| G4 --> G5 --> G7
    G5 -.->|Failure| G6 --> G5
    G8 --> G9 --> G10
    G10 -->|Yes| G11
    G10 -->|No| G12
    G8 --> G13 --> G14
''')

add('09-search-projection-recovery', '09 · Optional library projection jobs', 'OPTIONAL · SEARCH_PROJECTIONS_ENABLED',
    'app/db/search_repository.py app/services/search_indexing.py app/services/projection_indexes.py app/workers/search_indexing.py',
    'tests/test_search_indexing.py tests/search_projection_crash.py',
    'Projection failure does not invalidate the uploaded video. These jobs have their own state machine and accepted attempt per backend. Bad source/schema/model data needs explicit repair. This system is separate from core vector-count repair and from legacy lexical reconciliation.', '''
    Q1["Register accepted revision in finalizer transaction<br/>SHA-256 manifest + deterministic source key<br/>Unique video/revision/generation projection intent"]:::optional
    Q2["Generation configuration is immutable<br/>Changed backend/target/settings -> reject<br/>Semantic generation requires pinned model revision"]:::note
    Q3["Superseded revision or deletion -> obsolete<br/>Clear accepted attempt; lock parent then projection"]:::fail
    Q4["Dispatch indexing queue; reserve 60s window<br/>Claim pending/due retrying; max 3 attempts<br/>UUID attempt; 300s lease; 1800s deadline"]:::optional
    Q5["Heartbeat 100s + checkpoints<br/>Renew lease capped at deadline<br/>Stale ownership -> return stale"]:::normal
    Q6["Load accepted manifest and verify SHA/count<br/>Validate passage identity and aligned embeddings<br/>Reject model, dimension, timestamp or nonfinite mismatch"]:::normal
    Q7["Validate target schema/dimension; tolerate valid create races<br/>Write attempt-scoped IDs in bounded batches<br/>Verify counts, exact payloads and semantic vectors"]:::normal
    Q8["Guard parent/revision/token/lease/deadline<br/>Publish ready + accepted_attempt_id<br/>Readers use this exact identity"]:::safe
    Q9["Exception -> classify<br/>ValueError / KeyError / TypeError are permanent<br/>Other temporary failures use exponential jittered retry"]:::retry
    Q10["Expired indexing -> recover with same budget<br/>Permanent or exhausted -> failed<br/>Wrong token/obsolete -> no failure overwrite"]:::fail
    Q11["Reconcile: bounded backfill + oldest verification<br/>Network uncertainty keeps eligibility<br/>Bad source integrity -> failed and revoke acceptance"]:::normal
    Q12["Verified missing/corrupt ready projection<br/>request_repair checks expected accepted attempt<br/>Set pending, reset attempts, clear accepted identity"]:::retry
    Q13["Manual repair after correcting terminal cause<br/>python -m app.workers.search_indexing --repair JOB_UUID<br/>Allowed only for ready or failed current jobs"]:::note
    Q14["Attempt cleanup after deadline + 3600s<br/>Protect accepted/current indexing attempt<br/>Recheck hourly for late writes; record cleanup errors"]:::normal
    Q1 --> Q2 --> Q4 --> Q5 --> Q6 --> Q7 --> Q8
    Q4 -.->|Revision/deletion change| Q3
    Q6 -.->|Failure| Q9
    Q7 -.->|Failure| Q9
    Q9 --> Q10
    Q10 -->|Retry due| Q4
    Q8 --> Q11 --> Q12 --> Q4
    Q13 --> Q12
    Q10 -.-> Q14
    Q3 -.-> Q14
''')

add('10-search-serving-and-lexical', '10 · Optional search failure policy and legacy lexical repair', 'OPTIONAL · separate feature flags',
    'app/services/library_search.py app/services/search_policy.py app/api/search.py app/services/lexical_index.py app/services/lexical_reconciliation.py',
    'tests/test_library_search.py tests/test_search_policy.py tests/test_lexical_index.py',
    'Search results are revalidated against fresh Postgres state before return. A successful empty result differs from a failed backend. The legacy lexical reconciler runs only when Elasticsearch is configured and durable projections are disabled; it has different repair behavior.', '''
    S1["Library search explicitly enabled?<br/>Disabled -> 404; invalid request -> 422<br/>Authenticated owner scope only"]:::optional
    S2["Admission: 8 requests per process<br/>No slot -> 429 + Retry-After 1<br/>Total request budget 3s"]:::normal
    S3["Fresh Postgres scope; max 100 videos<br/>Scope too large -> 422; unavailable scope -> 503<br/>Only ready current revision + accepted attempt"]:::normal
    S4["Execute ONLY requested backend(s)<br/>Parallel hybrid branches; branch budget up to 2s<br/>Reserve 250ms; scope read up to 500ms"]:::normal
    S5["Reject invalid/nonfinite/foreign hits<br/>Re-read scope and generation before returning<br/>Drop deleted, revoked, superseded or repaired identities"]:::safe
    S6{"Any requested branch succeeded?"}:::decision
    S7["200, including a valid empty result<br/>Hybrid survivor allowed with degradation metadata<br/>Incomplete coverage notices are separate"]:::safe
    S8["No successful branch<br/>Failed/timed-out requested engine -> 503<br/>Nonempty scope, no eligible projection -> 409<br/>Truly empty authorized library -> 200"]:::fail
    S9["HTTP deadline can return before blocking calls end<br/>Admission stays occupied until all calls finish<br/>No unbounded pile-up of timed-out background work"]:::note
    S10["LEGACY lexical reconciliation<br/>Deterministic video/revision IDs and fingerprint<br/>Create not-ready manifest; tolerate creation races"]:::optional
    S11["Ready + matching count -> reuse<br/>Changed content without revision -> reject<br/>Bulk batches 250; reject partial write/refresh failure"]:::normal
    S12["Publish ready only after successful refresh<br/>Recheck accepted source after I/O<br/>Delete superseded revision; bounded cursors + per-item errors"]:::safe
    S13["Cleanup scans passages AND manifests<br/>Delete failure/version conflict/timeout -> retry later<br/>Partial/timed-out search never reported as success"]:::note
    S1 --> S2 --> S3 --> S4 --> S5 --> S6
    S6 -->|Yes| S7
    S6 -->|No| S8
    S4 -.-> S9
    S10 --> S11 --> S12 --> S13
''')

add('11-event-capture-and-feedback', '11 · Optional Kafka capture and interaction idempotency', 'OPTIONAL · opt-in analytics prototype',
    'app/services/search_events.py app/db/search_event_repository.py app/api/search.py app/api/search_analytics.py frontend/src/LibrarySearch.tsx',
    'tests/test_search_events.py tests/test_search_events_integration.py tests/search_analytics_browser.cjs',
    'No broker call occurs on the search request path. Optional capture may be skipped or fail before durable acceptance; a 25ms timeout is an unknown outcome because the bounded worker may commit later. Late capture does not enable feedback on an already returned response. Kafka contains content-free events, not raw queries.', '''
    A1["Capture flag + request opt-in required<br/>AUTH_REQUIRED or synthetic-only mode<br/>Disabled/no consent -> search continues without capture"]:::optional
    A2["Acquire one of 2 capture slots without waiting<br/>Busy/submission error -> no capture capability<br/>Separate DB pool: 2, no overflow, 10ms pool wait"]:::normal
    A3["Bound DB operations<br/>Connect 2s; statement 1000ms; lock 100ms<br/>Async caller waits at most 25ms, shielded worker"]:::normal
    A4["ONE transaction: context -> event outbox -> budget<br/>Postgres dependent CTE; rollback ALL if budget unavailable<br/>Event max 16KiB; pending outbox default 1GiB"]:::safe
    A5["Confirmed commit within wait -> interaction enabled<br/>Failure -> false; search response unchanged<br/>Timeout -> unknown; slot released only on completion"]:::note
    A6["Interaction request: owner + search ID + event UUID<br/>Strict schema and rank checks; HTTP body max 8KiB<br/>Context must belong to owner and be younger than 7 days"]:::normal
    A7{"Existing event ID?"}:::decision
    A8["Same search + payload fingerprint -> success replay<br/>Different payload/search -> 409 event_id_conflict<br/>No second budget charge or interaction increment"]:::safe
    A9["New interaction; max 100 per context<br/>Validate against stored result count<br/>Increment count + outbox + budget atomically"]:::normal
    A10["HTTP outcomes<br/>202 after acceptance; 404 missing/disabled context<br/>409 limit/conflict; 503 + Retry-After 1 on storage failure"]:::note
    A11["UI feedback retry reuses event_id<br/>Same search and same feedback value<br/>A changed feedback value creates a new event"]:::retry
    A12["Viewed/opened events are best effort<br/>No durable client retry queue<br/>User can retry a failed feedback selection"]:::note
    A13["Consent and query protection<br/>Query stored only in shared Postgres context<br/>Withdrawal clears query/share; global withdrawal timestamp<br/>Review rechecks ownership, revision and deletion"]:::note
    A1 --> A2 --> A3 --> A4 --> A5
    A5 -->|Capability available| A6 --> A7
    A7 -->|Yes| A8 --> A10
    A7 -->|No| A9 --> A10
    A10 -.->|Feedback failed| A11 --> A6
    A5 -.-> A12
    A6 -.-> A13
''')

add('12-event-publisher', '12 · Optional Kafka publisher and outbox expiry', 'OPTIONAL · analytics prototype',
    'app/workers/search_events.py app/db/search_event_repository.py',
    'tests/test_search_events.py tests/test_search_events_integration.py',
    'Producer idempotence does not make the Postgres-to-Kafka bridge exactly once. A crash after broker acceptance but before marking the outbox can republish the same event. Consumer receipts handle those duplicates. Seven-day expiry bounds retention; accepted events can expire during a sufficiently long outage.', '''
    K1["Outbox claim transaction<br/>Unpublished, younger than 7 days, lease absent/expired<br/>Oldest first, batch 100, FOR UPDATE SKIP LOCKED"]:::optional
    K2["Claim UUID + 30s lease; commit<br/>Concurrent publishers skip locked rows"]:::normal
    K3["Produce keyed by search_id<br/>enable.idempotence=true; acks=all<br/>Delivery timeout 10s; request timeout 5s; linger 5ms"]:::normal
    K4["Bound producer queue<br/>1000 messages / 16MiB; flush up to 11s<br/>Collect only successful delivery callbacks"]:::normal
    K5{"Broker ACK AND current live claim?"}:::decision
    K6["Atomic mark published + decrement pending bytes<br/>Match event ID/token; unpublished; unexpired lease<br/>Duplicate ACK cannot release budget twice"]:::safe
    K7["No mark on failure, unknown outcome or expired claim<br/>Outbox remains durable; reclaim after lease expiry<br/>Publisher loop catches errors and waits 250ms"]:::retry
    K8["Crash after Kafka ACK but before DB mark<br/>Possible duplicate publication<br/>Consumer receipt prevents duplicate effects: panel 13"]:::note
    K9["Maintenance each 60s<br/>Delete outbox rows older than 7 days in batches 1000<br/>Unpublished expiry decrements bytes and increments expired_events"]:::note
    K10["Retain published interaction IDs during context lifetime<br/>Expire contexts and old projections<br/>Live receipts/checkpoints are not pruned here"]:::note
    K11["Local bootstrap defaults<br/>3 partitions; replication factor 1; min ISR 1<br/>7-day / 1GiB-per-partition retention limits"]:::note
    K12["Local prototype only<br/>Single-replica acks=all is not high availability<br/>Retained data bounds replay and recovery"]:::fail
    K1 --> K2 --> K3 --> K4 --> K5
    K5 -->|Yes| K6
    K5 -->|No| K7 --> K1
    K6 -.->|Crash window| K8
    K7 -.->|Prolonged outage| K9 --> K10
    K3 -.-> K11 --> K12
''')

add('13-consumer-replay', '13 · Optional Kafka consumers, quarantine and replay', 'OPTIONAL · analytics prototype',
    'app/workers/search_events.py app/db/search_event_repository.py app/db/search_event_models.py',
    'tests/test_search_event_worker.py tests/test_search_events_rebalance.py tests/test_search_events_integration.py',
    'Analytics and review use independent consumer groups and receipt generations. Receipt identity is generation + event_id. Deduplication applies to database effects within a generation, not producer capture. An absent broker offset is allowed only when the DB checkpoint equals the retained low watermark; a broker offset without a DB checkpoint fails closed. Replay creates a separate shadow and never replaces the live dashboard.', '''
    N1["Assign partition using DB checkpoint + broker offset<br/>New group starts at retained low watermark<br/>Persist initial broker position before first event"]:::optional
    N2{"History consistent with retained bounds?"}:::decision
    N3["Fail closed on lost history<br/>Missing/expired offset; missing DB projection history;<br/>broker ahead of DB or DB beyond high watermark"]:::fail
    N4["Poll max 50 records or 100ms<br/>Auto commit and auto offset store disabled<br/>Revoke/lost partition -> discard uncommitted buffer"]:::normal
    N5["Validate batch order and strict event schema<br/>Max record 16KiB; malformed/null/oversized -> quarantine<br/>Quarantine keyed by generation/topic/partition/offset + digest"]:::normal
    N6["ONE DB transaction<br/>Checkpoint + sorted per-search advisory locks<br/>Receipt + projection OR quarantine"]:::safe
    N7{"Receipt generation + event_id already exists?"}:::decision
    N8["Duplicate -> skip projection effect<br/>Still advance checkpoint safely"]:::safe
    N9["Apply deterministic projection<br/>Out-of-order interactions retain attribution<br/>Latest feedback ordered by recorded_at + event_id"]:::normal
    N10["DB commit FIRST; then synchronous Kafka offset+1<br/>Commit separately per partition<br/>Crash/offset-commit failure -> replay; receipts deduplicate"]:::safe
    N11["DB failure -> transaction rollback, no offset commit<br/>Consumer restarts after 1s for operational exceptions<br/>ValueError/lost history stops for operator recovery"]:::retry
    N12["Replay into replay-UUID generation<br/>Capture current bounds or use saved manifest<br/>Validate topic, partitions and retained start/end"]:::optional
    N13["Read only saved interval; batches 50; deadline 600s<br/>Same receipt/projection/quarantine transaction<br/>Changed payload must be a new event; corrected handler may replay"]:::normal
    N14["Write manifest only after complete replay<br/>Bounds + event count + projection checksum<br/>Handler SHA + quarantine count; no live replacement"]:::safe
    N15["Retire only replay-UUID generations explicitly<br/>Remove their projections, receipts, quarantine and checkpoints<br/>Live generations cannot use shadow retirement"]:::note
    N1 --> N2
    N2 -->|No| N3
    N2 -->|Yes| N4 --> N5 --> N6 --> N7
    N7 -->|Yes| N8 --> N10
    N7 -->|No| N9 --> N10
    N6 -.->|Failure| N11 --> N1
    N10 -.->|Crash before offset commit| N1
    N3 -.->|Operator chooses bounded rebuild| N12 --> N13 --> N14 --> N15
''')

add('14-metrics-and-alerts', '14 · Conditional metrics, bounded I/O and alerts', 'Core; optional monitoring requires configuration',
    'app/services/metrics_service.py app/services/storage_service.py app/db/session.py app/workers/maintenance.py ops/alerts.yml ops/prometheus.yml ops/alertmanager.yml',
    'tests/test_conditional_storage.py tests/test_operational_safety.py',
    'Metrics are best effort and never establish processing success. Conditional writes merge different fields but the same field remains last-successful-writer. Alerts describe supplied rules; delivery is not configured by generating this board. The earlier prose mentioning eight alerts is older than the current fourteen-rule file.', '''
    M1["Read metrics JSON + ETag<br/>404 means absent; other read errors propagate"]:::normal
    M2["Merge updated fields<br/>Existing object: If-Match ETag<br/>New object: If-None-Match *"]:::normal
    M3{"Conditional write succeeds?"}:::decision
    M4["Success; preserve concurrent different fields"]:::safe
    M5["404 / 409 / 412 -> reload and merge again<br/>Maximum 8 conditional-write attempts<br/>Other error or exhausted attempts -> raise"]:::retry
    M6["safe_update_metrics logs and returns None<br/>Processing success/failure stays authoritative in Postgres"]:::note
    M7["Dependency I/O bounds<br/>S3 connect 5s / read 30s, standard 3 total attempts<br/>Core Qdrant timeout 10s"]:::normal
    M8["Core Postgres bounds<br/>Connect 5s; statement 30s; lock 5s<br/>pool_pre_ping; pool wait 10s"]:::normal
    M9["Independent maintenance operations<br/>Catch per-operation failure; rollback, record error<br/>Successful timestamps advance only on success"]:::normal
    M10["Core alerts<br/>Operations down 2m; dispatcher age >120s for 1m<br/>Queue age >300s for 2m; expired/progress >900s for 2m"]:::note
    M11["Failure and repair alerts<br/>Terminal failures in last 10m; dependencies failed for 2m<br/>GC/index errors or success age >1800s for 2m<br/>Backup success age >86400s for 5m"]:::note
    M12["Optional search alerts<br/>Lexical/projection/cleanup failures for 2m<br/>Unavailable ratio >5%, degraded >10%, p95 >1.5s for 5m<br/>Admission active >=8 for 2m"]:::note
    M13["Operational limits<br/>Default Alertmanager receiver sends nothing<br/>Configure/test receiver and external uptime monitor<br/>Same-host monitoring cannot report complete host loss"]:::fail
    M1 --> M2 --> M3
    M3 -->|Yes| M4
    M3 -->|Conflict| M5 --> M1
    M5 -->|Failure| M6
    M7 --> M9
    M8 --> M9
    M9 --> M10 --> M11 --> M12 --> M13
''')

add('15-backup-and-disaster-recovery', '15 · Verified backups and operator recovery', 'OPTIONAL · operations profile',
    'ops/backup.py docs/production_operations.md Dockerfile docker-compose.yml',
    'tests/test_backup.py tests/backup_restore_smoke.py',
    'These are implemented backup checks and a documented operator recovery procedure, not evidence of a completed cloud disaster-recovery drill. Six-hour logical snapshots do not provide point-in-time recovery. Restore commands are not run while creating this diagram.', '''
    B1["Backup requires separate S3 bucket<br/>Media AND backup buckets must have versioning enabled<br/>Failure -> do not mark backup successful"]:::optional
    B2["pg_dump custom format; unique object key<br/>Upload -> download -> verify SHA-256<br/>Commands have 1800s timeout"]:::normal
    B3["Real restore into unique disposable database<br/>pg_restore --exit-on-error<br/>Check schema revision, row counts and orphan chunks"]:::normal
    B4["Finally drop ONLY generated drill database<br/>Write checksum/restore manifest<br/>Record backup success only after all steps succeed"]:::safe
    B5["Any upload/checksum/restore error<br/>Record error; preserve last successful timestamp<br/>Loop retries next interval: default 21600s, minimum 300s"]:::retry
    B6["Operator disaster recovery<br/>Pause uploads, workers, dispatcher AND maintenance<br/>Preserve failed state for diagnosis"]:::note
    B7["Verify trusted backup + disposable restore drill<br/>Restore into separate recovery database<br/>Check compatible schema and reviewed migrations"]:::normal
    B8["Reconcile all referenced S3 object versions<br/>Rebuild accepted Qdrant vectors from manifests<br/>Validate searches, timestamps and ownership"]:::normal
    B9["Resume dispatcher/workers<br/>Expired leases recovered; pending jobs republished<br/>Inspect terminal/exhausted work before manual reprocessing"]:::normal
    B10["Restart GC only after artifact reconciliation<br/>Verify playback, uploads, alerts and backups<br/>Resume traffic after checks"]:::safe
    B11["Deployment safety<br/>Pause/drain old workers; backup; Alembic migration first<br/>Application startup exits on migration failure<br/>Old workers cannot participate in new token protocol"]:::note
    B1 --> B2 --> B3 --> B4
    B2 -.->|Failure| B5
    B3 -.->|Failure| B5
    B6 --> B7 --> B8 --> B9 --> B10
    B11 -.-> B6
''')

add('16-legacy-and-boundaries', '16 · Legacy compatibility and guarantee boundaries', 'Legacy and implementation limits',
    'app/workers/task.py app/db/video_repository.py docs/reliability_and_recovery.md docs/kafka_validation_results.md',
    'tests/test_video_repository_retry_state.py tests/test_pipeline_migration.py',
    'The legacy whole-video handler remains for old messages; new uploads use the durable pipeline. No unverified benchmark result is promoted to a passed gate. Kafka remains a locally validated prototype with outstanding acceptance gates, as recorded in the validation report.', '''
    Z1["Legacy whole-video Celery message<br/>Claim only uploaded or due retrying video<br/>Require NO durable pipeline_jobs for that video"]:::note
    Z2["Legacy claim increments video attempt count<br/>New token and owner; token-checked updates<br/>No claim -> skipped; stale token -> stale"]:::normal
    Z3["Legacy temporary claim error<br/>Uses request.retries + 1 and Celery task.retry<br/>Decorator max_retries = max attempts - 1"]:::retry
    Z4["Legacy processing error<br/>Persist retrying/failed on Video with matching token<br/>Then Celery task.retry with jittered countdown"]:::retry
    Z5["Not the new durable retry protocol<br/>No durable-row outbox bridge for legacy retry publication<br/>Do not describe legacy handler as equivalent recovery"]:::fail
    Z6["Core guarantee<br/>At-least-once delivery + idempotent claims<br/>Guarded acceptance; computation/writes may repeat"]:::safe
    Z7["Cross-system limit<br/>No Postgres/S3/Qdrant distributed transaction<br/>Fencing does not stop every stale external write"]:::note
    Z8["Terminal-work limit<br/>Core terminal failures are not automatically restarted<br/>Projection repair can explicitly reset its own budget"]:::note
    Z9["Timing limit<br/>UTC application clocks must be synchronized<br/>Defaults are code values, not measured recovery times<br/>Tune deadlines to real workloads"]:::note
    Z10["Kafka limit<br/>Optional pre-acceptance capture can be lost<br/>Timed-out capture can commit later<br/>Accepted-event accounting is not zero capture loss"]:::note
    Z11["Validation boundary<br/>No new tests or load runs are claimed here<br/>Existing tests are source references, not fresh passes<br/>Cloud HA and outstanding Kafka gates remain unverified"]:::note
    Z1 --> Z2
    Z1 -.->|Claim error| Z3
    Z2 -.->|Processing error| Z4 --> Z5
    Z6 --> Z7 --> Z8 --> Z9 --> Z10 --> Z11
''')


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    for panel in PANELS:
        panel['file'] = panel['slug'] + '.mmd'
        (ROOT / panel['file']).write_text('flowchart TB\n' + panel['code'] + '\n' + STYLES, encoding='utf-8')

    # All groups are editable on import. Keep each group independent: connecting
    # unrelated failure scenarios would incorrectly imply an execution sequence.
    combined = ['flowchart TB', '%% Independent numbered detail sections; arrows are local to each section.']
    for index, panel in enumerate(PANELS):
        combined.append(f'subgraph PANEL{index}["{panel["title"].replace("·", "-")}"]')
        combined.append(panel['code'])
        combined.append('end')
    combined.append(STYLES)
    (ROOT / 'vindex-reliability-all.mmd').write_text('\n'.join(combined) + '\n', encoding='utf-8')
    (ROOT / 'manifest.json').write_text(json.dumps(PANELS, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')

    lines = ['# Vindex reliability map', '',
             'Working-tree audit dated October 26, 2026. This extends the compact upload diagram with the implemented retry, idempotency, failure, cleanup, optional indexing, analytics, and operational recovery mechanisms.', '',
             'Open [the visual guide](index.html). Each numbered panel has an editable Mermaid source and a copy button. Paste the source onto an empty area of the Whimsical board. The combined source imports all sections, but Whimsical chooses their placement; separate panel pastes give the most readable board layout. Suggested order: core panels 01–08, optional search 09–10, optional analytics 11–13, operations and limits 14–16.', '',
             'The diagrams are an implementation map, not a record of newly run integration tests or benchmark acceptance. Optional paths require their feature configuration. All timing/budget values shown are code defaults unless explicitly described as constants.', '',
             '## Source and test traceability', '']
    repo = ROOT.parents[2]
    for panel in PANELS:
        lines.extend([f'### {panel["title"]}', '', f'**Scope:** {panel["scope"]}.', '',
                      f'[Diagram source]({panel["file"]}) · [Vector preview]({panel["slug"]}.svg)', '', panel['notes'], '', 'Implementation:', ''])
        for source in panel['sources']:
            assert (repo / source).exists(), source
            lines.append(f'- [{source}](../../../{source})')
        lines.extend(['', 'Existing evidence/test definitions (not executed by this documentation task):', ''])
        for test in panel['tests']:
            if (repo / test).exists():
                lines.append(f'- [{test}](../../../{test})')
        lines.append('')
    lines.extend(['## Import and validation', '',
                  'Whimsical supports `flowchart TB`, native editable nodes, subgraphs, and inline style classes. See [its Mermaid import documentation](https://whimsical.com/learn/boards/mermaid). The browser editing connection was unavailable in this chat, so these files have not been inserted into the remote board.', '',
                  'Run `python docs/diagrams/reliability-map/build_map.py` to rebuild the sources, then `node docs/diagrams/reliability-map/render_map.cjs` to parse every diagram and create the local visual guide. The renderer downloads the version-pinned Mermaid browser bundle once into this task\'s temporary directory.', '',
                  'For the current Kafka acceptance-gate evidence, see [the validation report](../../kafka_validation_results.md). This map does not reevaluate those gates. The latest handoff records no active benchmark; no benchmark or monitor was started for this documentation task.', ''])
    (ROOT / 'README.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'Wrote {len(PANELS)} diagrams, combined source, manifest, and traceability guide.')


if __name__ == '__main__':
    main()
