import os
from pathlib import Path


STORAGE_ROOT = Path(os.getenv("STORAGE_ROOT", "storage"))

RAW_VIDEO_DIR = STORAGE_ROOT / "raw_videos"
PROCESSED_VIDEO_DIR = STORAGE_ROOT / "processed_videos"
AUDIO_DIR = STORAGE_ROOT / "audio"
TRANSCRIPT_DIR = STORAGE_ROOT / "transcripts"
EMBEDDING_DIR = STORAGE_ROOT / "embeddings"


for directory in [
    RAW_VIDEO_DIR,
    PROCESSED_VIDEO_DIR,
    AUDIO_DIR,
    TRANSCRIPT_DIR,
    EMBEDDING_DIR,
]:
    directory.mkdir(parents=True, exist_ok=True)
