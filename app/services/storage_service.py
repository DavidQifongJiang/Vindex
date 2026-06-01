import boto3
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from fastapi.responses import RedirectResponse
from botocore.exceptions import ClientError

class S3Storage:
    def __init__(self):
        self.bucket = os.environ["S3_BUCKET_NAME"]
        self.client = boto3.client("s3", region_name=os.getenv("AWS_REGION"))

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
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read().decode("utf-8")

    def write_json(self, key: str, data, indent: int | None = None):
        self.client.put_object(
            Bucket=self.bucket,
            Key=key,
            Body=json.dumps(data, indent=indent).encode("utf-8"),
            ContentType="application/json",
        )

    def read_json(self, key: str):
        return json.loads(self.read_text(key))

    def presigned_url(self, key: str, seconds: int = 3600):
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
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