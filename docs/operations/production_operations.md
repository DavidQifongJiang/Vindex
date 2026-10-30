# Before deployment

Do not start the new workers against an old schema. Pause uploads, drain/stop
workers and the dispatcher, back up Postgres, then apply `alembic upgrade head`.
The API image now exits if migration fails instead of starting against a broken
schema. Restart the dispatcher and workers only after the migration succeeds.

## Required configuration

1. Enable S3 versioning on the media bucket and a separate backup bucket. Configure
   encryption, access policies, retention/noncurrent-version lifecycle, and abort
   incomplete multipart uploads. The application does not modify these policies.
2. Set `BACKUP_S3_BUCKET` and enable `docker compose --profile operations up -d backup`.
   The backup identity needs database read access and CREATEDB for disposable
   restore drills, plus backup-bucket read/write and versioning inspection on
   both buckets. Provision credentials through your deployment secret manager or
   instance role. Prefer a dedicated database identity via a service-specific
   environment override; do not broaden API privileges for backups.
3. Configure the receiver in `ops/alertmanager.yml`, test an alert, and confirm it
   reaches the intended on-call destination. The default receiver sends nothing.
   Prometheus/Alertmanager ports bind only to loopback; use a protected tunnel.
4. Add an external uptime monitor and search API latency/error-rate monitoring.
   Monitoring on the same EC2 instance cannot report that instance's total loss.
5. Set production Cognito settings and `AUTH_REQUIRED=true`; replace example
   database passwords. Confirm private videos cannot be accessed by another user.
6. Benchmark a representative long video and tune processing/media deadlines.
   Retain prefork workers in Linux deployments. Ensure clock synchronization.
7. Confirm a successful backup in `operation_states`, run a restore drill, and
   document the accepted recovery point and recovery time. Six-hour snapshots may
   lose changes since the last snapshot; configure managed backups/WAL archival
   separately when point-in-time recovery is required.

## Backup verification

The backup service uses Postgres 16 tools. It creates a custom-format snapshot,
uploads it under a unique key, downloads and verifies SHA-256, then actually
restores it into `vindex_restore_<random UUID>`. The drill checks schema revision,
table readability/counts, and orphan chunks. It drops only that generated
database, including on restore failure. It never restores into the live database.

The `.dump.json` companion records the checksum, restored schema revision, and
row counts. A failed upload, checksum check, or restore does not mark the backup
successful. The monitor alerts when the last verified backup is over 24 hours old.

To verify an existing trusted dump, mount it read-only into the backup image and
run `--verify-file /path/to/database.dump`. This creates a disposable database on
the configured database server and needs enough free disk for the restore.
Only restore trusted archives. Verify IAM access, S3 lifecycle, and actual remote
download in staging; the local smoke test uses a fake S3 client.

## Recovery sequence

1. Stop uploads, all workers, the dispatcher, and maintenance. Preserve the old
   database/storage for diagnosis. Do not run garbage collection while metadata
   and object storage represent different recovery points.
2. Download a trusted backup and its manifest. Verify SHA-256 and run the
   disposable restore drill before directing application traffic to recovered data.
3. Restore into a separately provisioned recovery database using the matching
   Postgres tooling. Check the schema revision and apply reviewed migrations.
4. Verify all referenced media/embedding artifacts. Recover missing or overwritten
   keys from S3 versions/independent backups. Bucket versioning is not a coordinated
   snapshot; uploads after the database backup may need manual reconciliation.
5. Point the application at the recovered database. Rebuild lost Qdrant content
   from accepted S3 embeddings using index repair; check representative searches
   and timestamps. Keep public traffic paused until these checks pass.
6. Resume the dispatcher and workers. Expired leases become retryable and pending
   database work is republished, even if Redis was lost. Check failed/exhausted
   work before deciding on a manual reprocessing operation.
7. Restart maintenance only after artifact reconciliation, test playback and
   owner-only access, then resume uploads. Confirm backup/alert delivery again.

## Evidence and limits

The automated suite covers real Postgres claims and migrations, real Redis/Celery
duplicate delivery, killed-worker lease recovery, stale publications, upload
conflicts, deadlines, conditional metrics retries, cleanup protection, and local
Qdrant repair. The backup smoke test uses real pg_dump/pg_restore with fake S3.
Media timeout tests kill a real test subprocess; media/model output in the full
service-flow test is stubbed. Run a Linux prefork hard-kill drill and real model/S3
failure tests in staging before calling the deployment production-validated.

S3 conditional writes rely on [AWS conditional-write semantics](https://docs.aws.amazon.com/AmazonS3/latest/userguide/conditional-writes.html).
Celery time limits depend on the [worker pool and platform](https://docs.celeryq.dev/en/latest/userguide/workers.html#time-limits).
