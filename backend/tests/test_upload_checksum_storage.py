"""S3 checksum contract: signed policy and service-verified object metadata."""
import base64
import hashlib
import json
import unittest
from unittest.mock import Mock

import boto3
from botocore.config import Config

from app.services.storage_service import S3Storage


class UploadChecksumStorageTest(unittest.TestCase):
    def setUp(self):
        self.storage = S3Storage.__new__(S3Storage)
        self.storage.bucket = "test-upload-bucket"
        self.storage.client = Mock()
        self.digest = hashlib.sha256(b"raw media").hexdigest()
        self.encoded = base64.b64encode(bytes.fromhex(self.digest)).decode("ascii")

    def test_generated_policy_binds_checksum_type_length_and_key(self):
        # Explicit dummy credentials keep this signing test entirely offline.
        self.storage._browser_client = boto3.client("s3", region_name="us-east-1",
            aws_access_key_id="test", aws_secret_access_key="test",
            config=Config(signature_version="s3v4"))
        post = self.storage.presigned_upload("raw_videos/test/attempt/file.mp4",
            content_type="video/mp4", size_bytes=9, seconds=60, sha256=self.digest)
        fields = post["fields"]
        conditions = json.loads(base64.b64decode(fields["policy"]))["conditions"]
        for name, value in (("key", "raw_videos/test/attempt/file.mp4"),
                            ("Content-Type", "video/mp4"),
                            ("x-amz-checksum-algorithm", "SHA256"),
                            ("x-amz-checksum-sha256", self.encoded)):
            self.assertEqual(fields[name], value)
            self.assertIn({name: value}, conditions)
        self.assertIn(["content-length-range", 9, 9], conditions)

    def test_head_requests_s3_verified_checksum(self):
        self.storage.client.head_object.return_value = {"ChecksumSHA256": self.encoded,
                                                       "ChecksumType": "FULL_OBJECT"}
        self.assertEqual(self.storage.object_sha256("snapshot"), self.digest)
        self.storage.client.head_object.assert_called_once_with(
            Bucket="test-upload-bucket", Key="snapshot", ChecksumMode="ENABLED")

    def test_missing_or_composite_checksum_is_not_a_verified_whole_file(self):
        for response in ({"Metadata": {"sha256": self.digest}},
                         {"ChecksumSHA256": self.encoded, "ChecksumType": "COMPOSITE"}):
            self.storage.client.head_object.return_value = response
            self.assertIsNone(self.storage.object_sha256("snapshot"))


if __name__ == "__main__":
    unittest.main()
