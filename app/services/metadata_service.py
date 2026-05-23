import json
from pathlib import Path


METADATA_PATH = Path("storage/videos.json")
METADATA_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_metadata():
    if not METADATA_PATH.exists():
        return {}

    with METADATA_PATH.open("r") as file:
        return json.load(file)


def save_metadata(metadata):
    with METADATA_PATH.open("w") as file:
        json.dump(metadata, file, indent=4)
