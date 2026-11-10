"""The opt-in cloud probe must fail closed and clean up after ambiguous writes."""
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from benchmarks import verify_direct_upload_s3 as probe


class S3UploadProbeTest(unittest.TestCase):
    def setUp(self):
        self.s3, self.sts = Mock(), Mock()
        self.sts.get_caller_identity.return_value = {"Account": "123456789012"}
        self.s3.get_bucket_versioning.return_value = {}
        self.s3.get_bucket_notification_configuration.return_value = {}
        self.s3.delete_objects.return_value = {}
        self.s3.list_objects_v2.return_value = {}
        self.s3.generate_presigned_post.side_effect = lambda **kwargs: {
            "url": "https://fixture.invalid/", "fields": {"key": kwargs["Key"]}}
        self.session = Mock()
        self.session.client.side_effect = lambda service, **kwargs: self.sts if service == "sts" else self.s3

    def run_probe(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "evidence.json"
            argv = ["probe", "--account", "123456789012", "--bucket", "test-bucket",
                    "--region", "us-east-1", "--origin", "https://app.invalid", "--output", str(output)]
            with patch.object(probe.sys, "argv", argv), patch.object(probe.boto3, "Session", return_value=self.session), \
                 patch.object(probe.requests, "post", side_effect=requests.Timeout) as post, redirect_stdout(io.StringIO()):
                self.assertEqual(probe.main(), 1)
            return json.loads(output.read_text()), post

    def test_wrong_account_never_uploads_or_deletes(self):
        self.sts.get_caller_identity.return_value = {"Account": "999999999999"}
        report, post = self.run_probe()
        self.assertFalse(report["passed"])
        post.assert_not_called()
        self.s3.head_bucket.assert_not_called()
        self.s3.delete_objects.assert_not_called()

    def test_versioning_and_event_notifications_stop_before_writes(self):
        for versioning, notifications in (({"Status": "Enabled"}, {}), ({"Status": "Suspended"}, {}),
                ({}, {"EventBridgeConfiguration": {}}), ({}, {"QueueConfigurations": [{"Id": "events"}]})):
            self.s3.get_bucket_versioning.return_value = versioning
            self.s3.get_bucket_notification_configuration.return_value = notifications
            report, post = self.run_probe()
            self.assertEqual(report["cleanup"], "not_started")
            post.assert_not_called()
            self.s3.delete_objects.assert_not_called()

    def test_timeout_after_possible_upload_deletes_only_the_run_key_and_preserves_evidence(self):
        report, post = self.run_probe()
        post.assert_called_once()
        self.assertEqual(report["failure"]["type"], "Timeout")
        self.assertEqual(report["cleanup"], "passed")
        deletion = self.s3.delete_objects.call_args.kwargs
        self.assertEqual(deletion["ExpectedBucketOwner"], "123456789012")
        self.assertEqual(deletion["Delete"]["Objects"], [{"Key": report["prefix"] + "valid.mp4"}])
        self.assertTrue(report["prefix"].startswith("upload-evaluation/"))
        self.assertEqual(self.s3.list_objects_v2.call_args.kwargs["Prefix"], report["prefix"])


if __name__ == "__main__":
    unittest.main()
