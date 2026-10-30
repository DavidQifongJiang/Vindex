"""Off-host logical backup with a restore drill into a disposable database."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
from time import sleep
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import boto3
from botocore.config import Config
import psycopg2
from psycopg2 import sql

logger = logging.getLogger(__name__)


def database_environment():
    env = dict(os.environ)
    url = env.get("DATABASE_URL")
    if url:
        parsed = urlsplit(url.replace("postgresql+psycopg2://", "postgresql://", 1))
        if parsed.scheme not in {"postgresql", "postgres"} or parsed.query:
            raise ValueError("Backup requires a Postgres URL without query options; use PGSSLMODE for TLS")
        for key, value in (("PGHOST", parsed.hostname), ("PGPORT", parsed.port or 5432),
                           ("PGUSER", unquote(parsed.username or "")),
                           ("PGPASSWORD", unquote(parsed.password or "")),
                           ("PGDATABASE", unquote(parsed.path.lstrip("/")))):
            env[key] = str(value)
    if not env.get("PGDATABASE"):
        raise ValueError("PGDATABASE or DATABASE_URL is required")
    env["PGCONNECT_TIMEOUT"] = "10"
    env["PGOPTIONS"] = "-c statement_timeout=1800000 -c lock_timeout=10000"
    return env


def connect(env, database=None):
    return psycopg2.connect(host=env.get("PGHOST", "postgres"), port=env.get("PGPORT", "5432"),
        user=env.get("PGUSER", "vindex"), password=env.get("PGPASSWORD"),
        dbname=database or env["PGDATABASE"], connect_timeout=10,
        sslmode=env.get("PGSSLMODE", "prefer"), options=env["PGOPTIONS"])


def run(command, env):
    # Credentials go through the environment, never command arguments or logs.
    subprocess.run(command, env=env, check=True, timeout=1800, capture_output=True)


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def restore_drill(path, env):
    database = "vindex_restore_" + uuid4().hex
    admin = connect(env)
    admin.autocommit = True
    created = False
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
            created = True
        run(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "--dbname", database, str(path)],
            {**env, "PGDATABASE": database})
        restored = connect(env, database)
        try:
            with restored.cursor() as cursor:
                cursor.execute("SELECT version_num FROM alembic_version")
                revision = cursor.fetchone()[0]
                counts = {}
                for table in ("videos", "video_search_chunks", "pipeline_jobs", "upload_requests"):
                    cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
                    counts[table] = cursor.fetchone()[0]
                cursor.execute("SELECT count(*) FROM video_search_chunks c LEFT JOIN videos v ON c.video_id=v.video_id WHERE v.video_id IS NULL")
                if cursor.fetchone()[0]:
                    raise RuntimeError("Restored database contains orphaned chunks")
                return {"revision": revision, "restored_rows": counts}
        finally:
            restored.close()
    finally:
        try:
            if created:
                with admin.cursor() as cursor:
                    cursor.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))
        finally:
            admin.close()


def record(env, error=None, details=None):
    connection = connect(env)
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("""INSERT INTO operation_states (name, updated_at, succeeded_at, error, details)
                VALUES ('backup', timezone('UTC', now()), CASE WHEN %s IS NULL THEN timezone('UTC', now()) END, %s, %s)
                ON CONFLICT (name) DO UPDATE SET updated_at=EXCLUDED.updated_at,
                succeeded_at=COALESCE(EXCLUDED.succeeded_at, operation_states.succeeded_at),
                error=EXCLUDED.error, details=COALESCE(EXCLUDED.details, operation_states.details)""",
                (error, error, json.dumps(details) if details else None))
    finally:
        connection.close()


def backup_once(env, client):
    bucket, media = os.environ.get("BACKUP_S3_BUCKET"), os.environ.get("S3_BUCKET_NAME")
    if not bucket or not media or bucket == media:
        raise ValueError("Set BACKUP_S3_BUCKET to a separate backup bucket and S3_BUCKET_NAME to the media bucket")
    for name in (bucket, media):
        if client.get_bucket_versioning(Bucket=name).get("Status") != "Enabled":
            raise RuntimeError("Versioning must be enabled on both media and backup buckets")
    key = f"postgres/{datetime.now(timezone.utc):%Y/%m/%d/%H%M%S}-{uuid4().hex}.dump"
    with TemporaryDirectory(prefix="vindex-backup-") as directory:
        path = Path(directory) / "database.dump"
        downloaded = Path(directory) / "downloaded.dump"
        run(["pg_dump", "--format=custom", "--no-owner", "--no-privileges", "--file", str(path)], env)
        checksum = digest(path)
        client.upload_file(str(path), bucket, key)
        client.download_file(bucket, key, str(downloaded))
        if digest(downloaded) != checksum:
            raise RuntimeError("Downloaded backup checksum mismatch")
        details = {"bucket": bucket, "key": key, "sha256": checksum, **restore_drill(downloaded, env)}
        client.put_object(Bucket=bucket, Key=key + ".json", Body=json.dumps(details).encode(), ContentType="application/json")
        record(env, details=details)
        return details


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--verify-file", type=Path)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    env = database_environment()
    if args.verify_file:
        print(json.dumps(restore_drill(args.verify_file, env)))
        return
    client = boto3.client("s3", region_name=os.getenv("AWS_REGION"),
        endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
        config=Config(connect_timeout=5, read_timeout=30, retries={"mode": "standard", "max_attempts": 3}))
    while True:
        try:
            details = backup_once(env, client)
            logger.info("Backup restored successfully: %s", details["key"])
        except Exception as exc:
            logger.exception("Backup or restore verification failed")
            record(env, error=type(exc).__name__)
            if not args.loop:
                raise
        if not args.loop:
            return
        sleep(max(300, int(os.getenv("BACKUP_INTERVAL_SECONDS", "21600"))))


if __name__ == "__main__":
    main()
