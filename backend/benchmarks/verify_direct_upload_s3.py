"""Opt-in live S3 contract probe. Creates tiny objects only in a unique test prefix.

Run from backend/. Never starts the API/workers or creates database jobs.
"""
import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import xml.etree.ElementTree as ET

import boto3
from botocore.config import Config
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.storage_service import S3Storage


def error_code(response):
    try:
        return ET.fromstring(response.content).findtext("Code")
    except ET.ParseError:
        return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--profile", default="default")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--node", default="node")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; preserve previous evidence")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"upload-evaluation/{uuid4().hex}/"
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "account": args.account,
              "bucket": args.bucket, "region": args.region, "origin": args.origin,
              "prefix": prefix, "checks": [], "cleanup": "not_started",
              "scope": "S3 contract with operator credentials; no application authentication or database jobs"}
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    storage = S3Storage.__new__(S3Storage)
    storage.bucket = args.bucket
    storage.client = storage._browser_client = session.client("s3",
        endpoint_url=f"https://s3.{args.region}.amazonaws.com",
        config=Config(signature_version="s3v4", s3={"addressing_style": "virtual"},
                      connect_timeout=5, read_timeout=30, retries={"total_max_attempts": 2}))
    s3 = storage.client
    keys = set()
    ready_for_writes = False

    def check(name, passed, **evidence):
        report["checks"].append({"name": name, "passed": bool(passed), **evidence})

    def post(name, payload, *, declared_size=None, signed_payload=None, overrides=None):
        key = prefix + name + ".mp4"
        keys.add(key)
        policy = storage.presigned_upload(key, content_type="video/mp4",
            size_bytes=len(payload) if declared_size is None else declared_size, seconds=120,
            sha256=hashlib.sha256(payload if signed_payload is None else signed_payload).hexdigest())
        fields = policy["fields"] | (overrides or {})
        if "key" in fields:
            assert fields["key"].startswith(prefix)
            keys.add(fields["key"])
        response = requests.post(policy["url"], data=fields,
            files={"file": ("probe.mp4", payload, "video/mp4")}, timeout=30)
        return key, response

    try:
        if session.client("sts").get_caller_identity()["Account"] != args.account:
            raise ValueError("AccountMismatch")
        s3.head_bucket(Bucket=args.bucket, ExpectedBucketOwner=args.account)
        versioning = s3.get_bucket_versioning(Bucket=args.bucket, ExpectedBucketOwner=args.account)
        if versioning.get("Status"):
            raise ValueError("VersionedBucketRequiresVersionAwareCleanup")
        notifications = s3.get_bucket_notification_configuration(Bucket=args.bucket, ExpectedBucketOwner=args.account)
        if any(notifications.get(key) for key in ("TopicConfigurations", "QueueConfigurations", "LambdaFunctionConfigurations")) or "EventBridgeConfiguration" in notifications:
            raise ValueError("BucketNotificationsRequireSeparateTestBucket")
        ready_for_writes = True
        payload = b"Vindex isolated S3 protocol probe.\n" * 32
        source, valid = post("valid", payload)
        check("signed_post", valid.status_code in (200, 201, 204), http_status=valid.status_code, error_code=error_code(valid))
        if valid.status_code not in (200, 201, 204):
            raise ValueError("ValidUploadRejected")
        check("stored_checksum", storage.object_sha256(source) == hashlib.sha256(payload).hexdigest())
        snapshot = prefix + "snapshot.mp4"
        keys.add(snapshot)
        storage.copy_object(source, snapshot)
        check("copy_preserves_verified_checksum", storage.object_sha256(snapshot) == hashlib.sha256(payload).hexdigest())
        copied = s3.get_object(Bucket=args.bucket, Key=snapshot, ExpectedBucketOwner=args.account)["Body"].read()
        check("copy_preserves_bytes", copied == payload)

        invalid, bad = post("bad-digest", b"X" + payload[1:], signed_payload=payload)
        check("wrong_checksum_rejected", bad.status_code == 400 and error_code(bad) == "BadDigest"
              and storage.object_size(invalid) is None, http_status=bad.status_code, error_code=error_code(bad))
        for name, data in (("too-large", payload + b"X"), ("too-small", payload[:-1])):
            key, response = post(name, data, declared_size=len(payload))
            check(name + "_rejected", 400 <= response.status_code < 500 and storage.object_size(key) is None,
                  http_status=response.status_code, error_code=error_code(response))
        tampered_key = prefix + "tampered-target.mp4"
        _, response = post("signed-key", payload, overrides={"key": tampered_key})
        check("key_tampering_rejected", response.status_code == 403 and storage.object_size(tampered_key) is None,
              http_status=response.status_code, error_code=error_code(response))

        browser_key = prefix + "browser.mp4"
        keys.add(browser_key)
        policy = storage.presigned_upload(browser_key, content_type="video/mp4", size_bytes=len(payload),
                                         seconds=120, sha256=hashlib.sha256(payload).hexdigest())
        completed = subprocess.run([args.node, str(Path(__file__).with_name("verify_s3_upload_browser.cjs"))],
            input=json.dumps({"origin": args.origin, "upload": policy, "payload": base64.b64encode(payload).decode()}),
            text=True, capture_output=True, timeout=60, env=os.environ.copy())
        browser = json.loads(completed.stdout) if completed.returncode == 0 else {"ok": False, "error": "BrowserProbeFailed"}
        check("browser_cors_post", browser.get("ok") and storage.object_sha256(browser_key) == hashlib.sha256(payload).hexdigest(),
              http_status=browser.get("status"), error=browser.get("error"))
    except Exception as exc:
        code = getattr(exc, "response", {}).get("Error", {}).get("Code") if isinstance(getattr(exc, "response", None), dict) else None
        report["failure"] = {"type": type(exc).__name__, "code": code}
    finally:
        if ready_for_writes:
            try:
                # Delete only exact keys generated by this run, never a bucket/prefix sweep.
                if keys:
                    result = s3.delete_objects(Bucket=args.bucket, ExpectedBucketOwner=args.account,
                        Delete={"Objects": [{"Key": key} for key in sorted(keys)]})
                    if result.get("Errors"):
                        raise RuntimeError("CleanupDeleteFailed")
                remaining = s3.list_objects_v2(Bucket=args.bucket, Prefix=prefix, ExpectedBucketOwner=args.account)
                report["cleanup"] = "passed" if not remaining.get("Contents") else "failed"
            except Exception:
                report["cleanup"] = "failed"
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["passed"] = not report.get("failure") and bool(report["checks"]) and all(c["passed"] for c in report["checks"]) and report["cleanup"] == "passed"
        with args.output.open("x", encoding="utf-8") as output:
            json.dump(report, output, indent=2)
        print(json.dumps(report, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
