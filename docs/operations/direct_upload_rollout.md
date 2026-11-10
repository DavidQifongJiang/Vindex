# Direct upload rollout and verification

The application already supports direct browser-to-S3 uploads. The API authorizes
an owner-scoped upload intent and signs an exact-size S3 POST. Completion creates
a server-only copy, then commits the video and its initial jobs in PostgreSQL.
Redis transports work after acceptance; PostgreSQL owns durable job state.

## File integrity

New frontend builds hash the file incrementally in a worker before creating the
intent. SHA-256 is part of the idempotency fingerprint and the signed S3 policy.
Completion checks S3's verified SHA-256 on the immutable copy before creating jobs.
Custom object metadata and multipart composite checksums are not accepted as a
whole-file SHA-256. The current protocol is one S3 POST, not S3 multipart upload.

Deploy the backend with `DIRECT_UPLOAD_REQUIRE_CHECKSUM=false` first. Deploy the
frontend with `VITE_UPLOAD_MODE=direct`, verify real S3 uploads, and only then set
`DIRECT_UPLOAD_REQUIRE_CHECKSUM=true` to require checksums for new intents. Existing
checksum-free sessions can still complete, but cached older clients cannot create
new intents in strict mode and receive a refresh instruction. The compatibility
period does not guarantee content-bound identity for older clients.

Hashing adds a complete local file read. Include that time in user-visible upload
latency measurements. Reads are bounded to 1 MiB and run off the main UI thread.
The same in-memory File reuses its digest on retry. Reloading the page does not
persist upload state or resume an interrupted transfer.

## Acceptance before production rollout

- Run backend tests against isolated PostgreSQL and Redis; run the production
  frontend build and browser suites, including the direct-upload worker scenario.
- In a dedicated staging bucket, test the actual regional S3 POST, CORS, signed
  checksum rejection, server-side copy checksum preservation, and HEAD checksum
  permissions. KMS-encrypted objects may require additional KMS permissions.
- Exercise a lost completion response and concurrent completion. Each logical
  upload must produce one video and one set of initial jobs, with its accepted
  input still readable. Keep uncertain snapshots for reference-aware GC.
- Compare both upload paths on equivalent infrastructure. Production API Gateway
  has a payload limit: do not use it as the legacy baseline for large videos.
  Browser fixtures and signing unit tests do not prove real AWS behavior or speed.

## Rollback

Each PR should remain independently revertible in reverse merge order. There is
no schema migration for checksum support. Disable the strict flag first if old
clients need recovery. Reverting the checksum frontend/backend pair restores
the preceding protocol; retain the ambiguous-commit recovery fix.

Do not switch large uploads back through the production API Gateway as a generic
rollback: its payload limit still applies. Prefer the last known-good direct-upload
build. Merging main triggers the existing production deployment workflow, so keep
PRs unmerged until the staged acceptance checks and rollout decision are recorded.

AWS contract references: [POST object checksum fields](https://docs.aws.amazon.com/AmazonS3/latest/API/RESTObjectPOST.html),
[copy checksum behavior](https://docs.aws.amazon.com/AmazonS3/latest/API/API_CopyObject.html),
[HEAD checksum retrieval](https://docs.aws.amazon.com/AmazonS3/latest/API/API_HeadObject.html).
