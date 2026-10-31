"""Fetch publisher English captions for the local 50-video query authoring set."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
from urllib.parse import urljoin

import requests

ROOT = Path(__file__).resolve().parent
DEST = ROOT / "lecture_captions"


class Tracks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "track" and attrs.get("srclang") == "en":
            self.urls.append(attrs["src"])


def fetch(video):
    page = requests.get(video["source_page"], timeout=(15, 60))
    page.raise_for_status()
    parser = Tracks()
    parser.feed(page.text)
    if not parser.urls:
        raise ValueError(f"No English caption track: {video['video_key']}")
    url = urljoin(page.url, parser.urls[0])
    response = requests.get(url, timeout=(15, 60))
    response.raise_for_status()
    if not response.text.lstrip('\ufeff').startswith("WEBVTT"):
        raise ValueError(f"Not WebVTT: {url}")
    path = DEST / (video["video_key"] + ".vtt")
    path.write_bytes(response.content)
    return {"video_key": video["video_key"], "source_page": video["source_page"],
            "caption_url": url, "file_path": str(path.relative_to(ROOT.parent)).replace('\\', '/'),
            "sha256": sha256(response.content).hexdigest()}


def main():
    manifest = json.loads((ROOT / "open_lectures_50_manifest.json").read_text())
    DEST.mkdir(exist_ok=True)
    with ThreadPoolExecutor(max_workers=4) as pool:
        rows = list(pool.map(fetch, manifest["existing_videos"] + manifest["new_videos"]))
    (DEST / "manifest.json").write_text(json.dumps({
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "attribution": "MIT OpenCourseWare; authors and terms on each source page",
        "terms_url": "https://ocw.mit.edu/pages/privacy-and-terms-of-use/",
        "captions": rows}, indent=2) + '\n', encoding="utf-8")
    print(f"Downloaded {len(rows)} official English caption tracks")


if __name__ == "__main__":
    main()
