from io import BytesIO
import json
import unittest

import boto3
from botocore.stub import Stubber, ANY

from app.services.storage_service import S3Storage


class ConditionalStorageTest(unittest.TestCase):
    def setUp(self):
        self.storage = S3Storage.__new__(S3Storage)
        self.storage.bucket = "test-media"
        self.storage.client = boto3.client("s3", region_name="us-east-1",
            aws_access_key_id="test", aws_secret_access_key="test")
        self.addCleanup(self.storage.client.close)
        self.stub = Stubber(self.storage.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)

    def test_metrics_create_uses_if_none_match(self):
        self.stub.add_response("put_object", {}, {"Bucket": "test-media", "Key": "metrics/v1.json",
            "Body": ANY, "ContentType": "application/json", "IfNoneMatch": "*"})
        self.storage.write_json_conditional("metrics/v1.json", {}, None)
        self.stub.assert_no_pending_responses()

    def test_metrics_update_reuses_the_etag_from_the_read(self):
        self.stub.add_response("get_object", {"Body": BytesIO(json.dumps({"search": {}}).encode()), "ETag": '"version-1"'},
            {"Bucket": "test-media", "Key": "metrics/v1.json"})
        data, etag = self.storage.read_json_versioned("metrics/v1.json")
        self.stub.add_response("put_object", {}, {"Bucket": "test-media", "Key": "metrics/v1.json",
            "Body": ANY, "ContentType": "application/json", "IfMatch": '"version-1"'})
        self.storage.write_json_conditional("metrics/v1.json", data, etag)
        self.stub.assert_no_pending_responses()

    def test_missing_metrics_are_distinct_from_access_denied(self):
        self.stub.add_client_error("get_object", "NoSuchKey", http_status_code=404)
        self.assertEqual(self.storage.read_json_versioned("missing"), (None, None))
        self.stub.add_client_error("get_object", "AccessDenied", http_status_code=403)
        with self.assertRaises(self.storage.client.exceptions.ClientError):
            self.storage.read_json_versioned("private")
