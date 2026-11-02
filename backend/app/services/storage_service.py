import boto3
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.responses import RedirectResponse
from botocore.config import Config
from botocore.exceptions import ClientError

class S3Storage:
    def __init__(self):
        self.bucket = os.environ["S3_BUCKET_NAME"]
        self.client = boto3.client(
            "s3",
            region_name=os.getenv("AWS_REGION"),
            endpoint_url=os.getenv("S3_ENDPOINT_URL") or None,
            config=Config(signature_version="s3v4", connect_timeout=5, read_timeout=30,
                          retries={"mode": "standard", "total_max_attempts": 3}),
        )

        self._browser_client = None

    def _upload_client(self):
        """Client whose presigned URLs a browser can POST to without an S3 redirect.

        The default global endpoint answers cross-region POSTs with a redirect,
        which browsers refuse to follow for CORS uploads. Signing against the
        bucket's regional virtual-hosted endpoint avoids that.
        """
        if self._browser_client is None:
            region = os.getenv("AWS_REGION")
            endpoint = os.getenv("S3_ENDPOINT_URL") or (f"https://s3.{region}.amazonaws.com" if region else None)
            self._browser_client = boto3.client(
                "s3", region_name=region, endpoint_url=endpoint,
                config=Config(signature_version="s3v4",
                              s3={"addressing_style": "path" if os.getenv("S3_ENDPOINT_URL") else "virtual"}),
            )
        return self._browser_client

    def presigned_upload(self, key: str, *, content_type: str, size_bytes: int, seconds: int):
        """Presigned POST that only accepts this exact key, content type and byte count."""
        return self._upload_client().generate_presigned_post(
            Bucket=self.bucket,
            Key=key,
            Fields={"Content-Type": content_type},
            Conditions=[{"Content-Type": content_type}, ["content-length-range", size_bytes, size_bytes]],
            ExpiresIn=seconds,
        )

    def copy_object(self, source_key: str, target_key: str):
        """Server-side copy (no download); a snapshot of the source at copy time."""
        self.client.copy_object(Bucket=self.bucket, Key=target_key,
                                CopySource={"Bucket": self.bucket, "Key": source_key},
                                MetadataDirective="COPY")

    def object_size(self, key: str) -> int | None:
        try:
            return self.client.head_object(Bucket=self.bucket, Key=key)["ContentLength"]
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None
            raise

    def upload_fileobj(self, fileobj, key: str):
        self.client.upload_fileobj(fileobj, self.bucket, key)

    def upload_file(self, local_path: Path, key: str):
        self.client.upload_file(str(local_path), self.bucket, key)

    def download_file(self, key: str, local_path: Path):
        self.client.download_file(self.bucket, key, str(local_path))

    def write_text(self, key: str, text: str):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=text.encode("utf-8"),
            ContentType="text/plain",
        )

    def read_text(self, key: str) -> str:
        return self.read_bytes(key).decode("utf-8")

    def read_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        try:
            return response["Body"].read()
        finally:
            response["Body"].close()

    def write_json(self, key: str, data, indent: int | None = None):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(data, indent=indent).encode("utf-8"),
            ContentType="application/json",
        )

    def read_json(self, key: str):
        return json.loads(self.read_text(key))

    def read_json_versioned(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 404:
                return None, None
            raise
        return json.loads(response["Body"].read()), response["ETag"]

    def write_json_conditional(self, key, value, etag):
        condition = {"IfMatch": etag} if etag is not None else {"IfNoneMatch": "*"}
        self.client.put_object(Bucket=self.bucket, Key=key, Body=json.dumps(value).encode(),
                               ContentType="application/json", **condition)

    def list_objects(self, prefix):
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            yield from page.get("Contents", [])

    def scan_objects(self, prefix, cursor=None, limit=200):
        kwargs = {"Bucket": self.bucket, "Prefix": prefix, "MaxKeys": limit}
        if cursor:
            kwargs["ContinuationToken"] = cursor
        response = self.client.list_objects_v2(**kwargs)
        return response.get("Contents", []), response.get("NextContinuationToken")

    def presigned_url(self, key: str, seconds: int = 3600, response_content_type: str | None = None):
        params = {"Bucket": self.bucket, "Key": key}
        if response_content_type:
            params["ResponseContentType"] = response_content_type

        return self.client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=seconds,
        )
    
    def save_upload(self, fileobj, key: str, max_bytes: int):
        copied_bytes = 0

        with TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir) / Path(key).name

            with temp_path.open("wb") as target:
                while chunk := fileobj.read(1024 * 1024):
                    copied_bytes += len(chunk)
                    if copied_bytes > max_bytes:
                        raise ValueError("Uploaded video is too large")
                    target.write(chunk)

            self.upload_file(temp_path, key)

        return copied_bytes


    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
            return True
        except ClientError as exc:
            status_code = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if status_code == 404:
                return False
            raise

    def delete_many(self, keys: list[str]):
        unique_keys = sorted({key for key in keys if key})

        if not unique_keys:
            return 0

        deleted_count = 0
        for index in range(0, len(unique_keys), 1000):
            batch = unique_keys[index:index + 1000]
            response = self.client.delete_objects(
                Bucket=self.bucket,
                Delete={
                    "Objects": [{"Key": key} for key in batch],
                    "Quiet": True,
                },
            )
            errors = response.get("Errors", [])
            if errors:
                failed_keys = ", ".join(error.get("Key", "unknown") for error in errors[:5])
                raise RuntimeError(f"Failed to delete S3 objects: {failed_keys}")

            deleted_count += len(batch)

        return deleted_count


    def delete_prefix(self, prefix: str):
        if not prefix.endswith("/") or prefix.count("/") < 2:
            raise ValueError("Cleanup requires a video-specific prefix")
        deleted = 0
        for page in self.client.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix):
            deleted += self.delete_many([item["Key"] for item in page.get("Contents", [])])
        return deleted

    def file_response(self, key: str, media_type: str):
        url = self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "ResponseContentType": media_type,
            },
            ExpiresIn=int(os.getenv("S3_PRESIGNED_URL_SECONDS", "3600")),
        )
        return RedirectResponse(url=url)
    
def get_storage():
    return S3Storage()
