"""Run inside ops/Dockerfile.backup against a disposable vindex_test database."""
from pathlib import Path
import os
import sys
from tempfile import TemporaryDirectory
from uuid import uuid4

from psycopg2 import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from ops.backup import backup_once, connect, database_environment, run

COUNTED_TABLES = ("videos", "video_search_chunks", "pipeline_jobs", "upload_requests")


class FakeS3:
    def __init__(self):
        self.objects = {}

    def get_bucket_versioning(self, **kwargs):
        return {"Status": "Enabled"}

    def upload_file(self, path, bucket, key):
        self.objects[(bucket, key)] = Path(path).read_bytes()

    def download_file(self, bucket, key, path):
        Path(path).write_bytes(self.objects[(bucket, key)])

    def put_object(self, *, Bucket, Key, Body, **kwargs):
        self.objects[(Bucket, Key)] = Body


def source_state(connection):
    with connection, connection.cursor() as cursor:
        cursor.execute("SELECT version_num FROM alembic_version")
        revision = cursor.fetchone()[0]
        counts = {}
        for table in COUNTED_TABLES:
            cursor.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table)))
            counts[table] = cursor.fetchone()[0]
    return revision, counts


def assert_uploaded_dump_contains(dump, env, video_id):
    # restore_drill drops its database before returning, so restore the uploaded
    # bytes separately to check the seeded row itself rather than a row count.
    database = "vindex_smoke_" + uuid4().hex
    admin = connect(env)
    admin.autocommit = True
    try:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(database)))
        with TemporaryDirectory(prefix="vindex-smoke-") as directory:
            path = Path(directory) / "uploaded.dump"
            path.write_bytes(dump)
            run(["pg_restore", "--exit-on-error", "--no-owner", "--no-privileges", "--dbname", database, str(path)],
                {**env, "PGDATABASE": database})
        restored = connect(env, database)
        try:
            with restored, restored.cursor() as cursor:
                cursor.execute("SELECT status FROM videos WHERE video_id=%s", (video_id,))
                assert cursor.fetchall() == [("uploaded",)], "Seeded video did not survive the restore"
        finally:
            restored.close()
    finally:
        with admin.cursor() as cursor:
            cursor.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        admin.close()


def main():
    env = database_environment()
    if env["PGDATABASE"] != "vindex_test":
        raise RuntimeError("Restore smoke test requires the disposable vindex_test database")
    expected_head = os.environ.get("EXPECTED_ALEMBIC_HEAD")
    if not expected_head:
        raise RuntimeError("Set EXPECTED_ALEMBIC_HEAD to the repository's migration head")
    connection = connect(env)
    video_id = str(uuid4())
    client = FakeS3()
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO videos (video_id, status) VALUES (%s, 'uploaded')", (video_id,))
        source_revision, source_counts = source_state(connection)
        assert source_revision == expected_head, (source_revision, expected_head)
        os.environ.update(BACKUP_S3_BUCKET="test-backups", S3_BUCKET_NAME="test-media")
        details = backup_once(env, client)
        assert details["revision"] == source_revision, details
        assert details["restored_rows"] == source_counts, (details, source_counts)
        assert_uploaded_dump_contains(client.objects[("test-backups", details["key"])], env, video_id)
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'vindex_restore_%%'")
            assert cursor.fetchone()[0] == 0, "Disposable restore database was not cleaned up"
            cursor.execute("SELECT error, succeeded_at FROM operation_states WHERE name='backup'")
            error, succeeded = cursor.fetchone()
            assert error is None and succeeded is not None
        print("Real pg_dump, checksum round-trip, pg_restore, seeded-row and source-match checks, and cleanup passed (S3 mocked).")
    finally:
        with connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM videos WHERE video_id=%s", (video_id,))
        connection.close()


if __name__ == "__main__":
    main()
