"""Download publisher-linked lecture videos into the existing benchmark folder."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime, timezone
from hashlib import sha256, md5
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import struct
import time
from urllib.parse import urljoin, urlparse, unquote

import requests

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "vindex_benchmark_videos"
CATALOG = ROOT / "benchmarks" / "open_lectures_50_manifest.json"
COURSES = [
    ("6-006-introduction-to-algorithms-fall-2011", "mit6006", 11, 24),
    ("6-046j-design-and-analysis-of-algorithms-spring-2015", "mit6046", 1, 13),
    ("6-034-artificial-intelligence-fall-2010", "mit6034", 1, 13),
]


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.current = [], None

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.current = [dict(attrs).get("href", ""), ""]

    def handle_data(self, data):
        if self.current:
            self.current[1] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current:
            self.links.append(tuple(self.current))
            self.current = None


def links(url):
    response = requests.get(url, timeout=(15, 60))
    response.raise_for_status()
    parser = Links()
    parser.feed(response.text)
    return [(urljoin(url, href), " ".join(title.split())) for href, title in parser.links]


def discover():
    rows = []
    for slug, prefix, first, last in COURSES:
        course_url = "https://ocw.mit.edu/courses/" + slug + "/"
        download_links = links(course_url + "download/")
        pages = [u for u, _ in download_links if "/resources/" in u and "lecture-videos" in u]
        if not pages:
            raise ValueError(f"No publisher lecture list found: {slug}")
        candidates = links(pages[0])
        pending = None
        for url, title in candidates:
            if urlparse(url).path.lower().endswith(".mp4"):
                pending = url
            match = re.match(r"Lecture\s+(\d+):?\s*(.*)", title, re.I)
            if match and pending:
                number = int(match[1])
                if first <= number <= last and not any(r["video_key"] == f"{prefix}_lecture{number:02d}" for r in rows):
                    name = re.sub(r"[^a-z0-9]+", "_", match[2].lower()).strip("_")[:100]
                    if prefix == "mit6006":
                        existing = list(DEST.glob(f"{number:02d}_*.mp4"))
                    else:
                        existing = []
                    path = existing[0] if existing else DEST / f"{prefix}_{number:02d}_{name}.mp4"
                    rows.append({"video_key": f"{prefix}_lecture{number:02d}", "course": slug,
                        "lecture_number": number, "title": title, "source_page": url, "download_url": pending,
                        "file_path": path.relative_to(ROOT).as_posix(), "license": "CC BY-NC-SA 4.0",
                        "license_url": "https://ocw.mit.edu/pages/privacy-and-terms-of-use/", "status": "pending"})
                pending = None
        print(slug, sum(r["course"] == slug for r in rows), flush=True)
    if len(rows) != 40 or len({r["download_url"] for r in rows}) != 40:
        raise ValueError(f"Expected 40 distinct new lecture downloads, found {len(rows)}")
    CATALOG.write_text(json.dumps({"created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Local noncommercial retrieval evaluation; source attribution retained; videos unmodified.",
        "existing_full_videos": 10, "new_videos": rows}, indent=2), encoding="utf-8")


def duration(path):
    # Read MP4 movie-header duration without decoding or requiring ffmpeg.
    with path.open("rb") as stream:
        def boxes(end):
            while stream.tell() + 8 <= end:
                start = stream.tell()
                size, kind = struct.unpack(">I4s", stream.read(8))
                header = 8
                if size == 1:
                    size = struct.unpack(">Q", stream.read(8))[0]
                    header = 16
                if size == 0:
                    size = end - start
                if size < header or start + size > end:
                    raise ValueError("Invalid MP4 box")
                if kind == b"moov":
                    value = boxes(start + size)
                    if value:
                        return value
                elif kind == b"mvhd":
                    version = stream.read(4)[0]
                    stream.read(16 if version == 1 else 8)
                    scale = struct.unpack(">I", stream.read(4))[0]
                    ticks = struct.unpack(">Q" if version == 1 else ">I", stream.read(8 if version == 1 else 4))[0]
                    return ticks / scale
                stream.seek(start + size)
            return None
        value = boxes(path.stat().st_size)
        if not value or value < 60:
            raise ValueError("Missing valid lecture duration")
        return value


def download(row):
    path = ROOT / row["file_path"]
    path.resolve().relative_to(DEST.resolve())
    if path.exists():
        seconds = duration(path)
    else:
        url = row["download_url"].replace("http://", "https://", 1)
        if urlparse(url).hostname not in {"archive.org", "www.archive.org", "ocw.mit.edu"}:
            raise ValueError("Unexpected publisher download host")
        partial = path.with_suffix(".mp4.part")
        def finish():
            seconds = duration(partial)
            for retry in range(6):
                try:
                    partial.rename(path)
                    return seconds
                except PermissionError:
                    if retry == 5:
                        raise
                    time.sleep(1)
        for attempt in range(4):
            try:
                if shutil.disk_usage(DEST).free < 8 * 1024**3:
                    raise OSError("Preserving at least 8 GiB free disk space")
                offset = partial.stat().st_size if partial.exists() else 0
                with requests.get(url, headers={"Range": f"bytes={offset}-"} if offset else {}, stream=True, timeout=(30, 90)) as response:
                    if response.status_code == 416 and response.headers.get("Content-Range") == f"bytes */{offset}":
                        seconds = finish()
                        break
                    response.raise_for_status()
                    append = offset > 0 and response.status_code == 206
                    if append and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                        raise ValueError("Invalid resume offset")
                    with partial.open("ab" if append else "wb") as output:
                        for block in response.iter_content(1024 * 1024):
                            output.write(block)
                seconds = finish()
                break
            except (requests.RequestException, ValueError) as exc:
                if attempt == 3:
                    raise
                print(row["video_key"], "retry", type(exc).__name__, flush=True)
                time.sleep(2 ** attempt)
    digest = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    result = dict(row, status="verified", bytes=path.stat().st_size, duration_seconds=seconds,
                  sha256=digest.hexdigest(), verified_at=datetime.now(timezone.utc).isoformat())
    result.pop("error", None)
    return result


def run():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    rows = catalog["new_videos"]
    with ThreadPoolExecutor(max_workers=3) as pool:
        pending = {pool.submit(download, row): index for index, row in enumerate(rows)}
        for future in as_completed(pending):
            index = pending[future]
            try:
                rows[index] = future.result()
                print("VERIFIED", rows[index]["video_key"], round(rows[index]["duration_seconds"] / 60, 1), "min", flush=True)
            except Exception as exc:
                rows[index].update(status="failed", error=str(exc))
                print("FAILED", rows[index]["video_key"], type(exc).__name__, flush=True)
            CATALOG.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    if any(r["status"] != "verified" for r in rows):
        raise RuntimeError("Some downloads incomplete; rerun to resume")
    print("COMPLETE", len(rows), "new videos", sum(r["bytes"] for r in rows), "bytes", flush=True)


def verify_inventory():
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    original = []
    course = COURSES[0][0]
    pending = None
    for url, title in links(f"https://ocw.mit.edu/courses/{course}/resources/lecture-videos/"):
        if urlparse(url).path.lower().endswith(".mp4"):
            pending = url
        match = re.match(r"Lecture\s+(\d+):", title)
        if match and pending:
            number = int(match[1])
            if number <= 10:
                matches = list(DEST.glob(f"{number:02d}_*.mp4"))
                if len(matches) != 1:
                    raise ValueError("Ambiguous original video")
                original.append({"video_key": f"mit6006_lecture{number:02d}", "course": course,
                    "lecture_number": number, "title": title, "source_page": url, "download_url": pending,
                    "file_path": matches[0].relative_to(ROOT).as_posix(), "existing_before_expansion": True})
            pending = None
    rows = original + catalog["new_videos"]
    if len(rows) != 50:
        raise ValueError("Expected 50 distinct full lectures")
    metadata = {}
    for row in rows:
        path = ROOT / row["file_path"]
        parts = urlparse(row["download_url"]).path.split("/")
        item, name = parts[2], unquote("/".join(parts[3:]))
        if item not in metadata:
            response = requests.get("https://archive.org/metadata/" + item, timeout=(20, 90))
            response.raise_for_status()
            metadata[item] = response.json()
        source = next(f for f in metadata[item]["files"] if f["name"] == name)
        if path.stat().st_size != int(source["size"]):
            raise ValueError("Source size mismatch: " + path.name)
        sha, checksum = sha256(), md5()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                sha.update(block)
                checksum.update(block)
        if checksum.hexdigest() != source["md5"]:
            raise ValueError("Publisher-host checksum mismatch: " + path.name)
        row.update(bytes=path.stat().st_size, duration_seconds=duration(path), sha256=sha.hexdigest(),
            archive_md5=checksum.hexdigest(), status="source_checksum_verified",
            archive_metadata_url="https://archive.org/metadata/" + item,
            archive_license=metadata[item]["metadata"].get("licenseurl"),
            attribution="MIT OpenCourseWare, " + row["course"] + ", " + row["title"],
            terms_url="https://ocw.mit.edu/pages/privacy-and-terms-of-use/")
        print("SOURCE VERIFIED", row["video_key"], flush=True)
    catalog["existing_videos"] = original
    catalog["inventory_verified_at"] = datetime.now(timezone.utc).isoformat()
    catalog["total_full_videos"] = len(rows)
    catalog["total_hours"] = sum(r["duration_seconds"] for r in rows) / 3600
    catalog["total_bytes"] = sum(r["bytes"] for r in rows)
    CATALOG.write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    fields = ["video_key", "file_path", "description", "course", "duration_seconds", "file_size_bytes", "sha256", "source_page"]
    with (ROOT / "benchmarks/videos_library_50.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({"video_key": row["video_key"], "file_path": row["file_path"], "description": row["title"],
                "course": row["course"], "duration_seconds": row["duration_seconds"], "file_size_bytes": row["bytes"],
                "sha256": row["sha256"], "source_page": row["source_page"]})
    print("INVENTORY", len(rows), "videos", round(catalog["total_hours"], 2), "hours", round(catalog["total_bytes"] / 1e9, 2), "GB", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["discover", "download", "verify"])
    args = parser.parse_args()
    {"discover": discover, "download": run, "verify": verify_inventory}[args.action]()
