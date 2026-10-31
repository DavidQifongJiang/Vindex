import os
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from ops import backup


class BackupTest(TestCase):
    def test_credentials_are_decoded_and_kept_out_of_commands(self):
        with patch.dict(os.environ, {"DATABASE_URL": "postgresql+psycopg2://user:p%40ss@db:5432/vindex"}, clear=True):
            env = backup.database_environment()
        self.assertEqual(env["PGPASSWORD"], "p@ss")
        self.assertEqual(env["PGDATABASE"], "vindex")
        with patch("ops.backup.subprocess.run") as run:
            backup.run(["pg_dump", "--format=custom"], env)
        self.assertNotIn("p@ss", str(run.call_args.args))
        self.assertEqual(run.call_args.kwargs["env"]["PGPASSWORD"], "p@ss")

    def test_backup_requires_separate_versioned_buckets(self):
        client = Mock()
        for bucket in ("", "media"):
            with patch.dict(os.environ, {"BACKUP_S3_BUCKET": bucket, "S3_BUCKET_NAME": "media"}):
                with self.assertRaises(ValueError):
                    backup.backup_once({}, client)
        client.get_bucket_versioning.return_value = {}
        with patch.dict(os.environ, {"BACKUP_S3_BUCKET": "backup", "S3_BUCKET_NAME": "media"}):
            with self.assertRaises(RuntimeError):
                backup.backup_once({}, client)
        client.upload_file.assert_not_called()

    def test_bad_download_never_records_backup_success(self):
        client = Mock()
        client.get_bucket_versioning.return_value = {"Status": "Enabled"}
        client.download_file.side_effect = lambda bucket, key, path: Path(path).write_bytes(b"corrupted")
        def dump(command, env):
            Path(command[-1]).write_bytes(b"database snapshot")
        with patch.dict(os.environ, {"BACKUP_S3_BUCKET": "backup", "S3_BUCKET_NAME": "media"}), \
             patch("ops.backup.run", side_effect=dump), patch("ops.backup.record") as record, \
             patch("ops.backup.restore_drill") as restore:
            with self.assertRaises(RuntimeError):
                backup.backup_once({}, client)
        record.assert_not_called()
        restore.assert_not_called()
