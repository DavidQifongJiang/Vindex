"""Run inside ops/Dockerfile.backup against a disposable vindex_test database."""
from pathlib import Path
import os
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ops.backup import backup_once, connect, database_environment


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


def main():
    env = database_environment()
    if env["PGDATABASE"] != "vindex_test":
        raise RuntimeError("Restore smoke test requires the disposable vindex_test database")
    connection = connect(env)
    video_id = str(uuid4())
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO videos (video_id, status) VALUES (%s, 'uploaded')", (video_id,))
        os.environ.update(BACKUP_S3_BUCKET="test-backups", S3_BUCKET_NAME="test-media")
        details = backup_once(env, FakeS3())
        assert details["restored_rows"]["videos"] >= 1, details
        assert details["revision"] == "20260905_0005", details
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM pg_database WHERE datname LIKE 'vindex_restore_%%'")
            assert cursor.fetchone()[0] == 0, "Disposable restore database was not cleaned up"
            cursor.execute("SELECT error, succeeded_at FROM operation_states WHERE name='backup'")
            error, succeeded = cursor.fetchone()
            assert error is None and succeeded is not None
        print("Real pg_dump, checksum round-trip, pg_restore, restored-data checks, and cleanup passed (S3 mocked).")
    finally:
        with connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM videos WHERE video_id=%s", (video_id,))
        connection.close()


if __name__ == "__main__":
    main()
