from __future__ import annotations

import argparse
from pathlib import Path

from benchmark_common import BENCHMARK_DIR, read_csv_rows, resolve_repo_path, video_metadata, write_csv_rows


VIDEOS_CSV = BENCHMARK_DIR / "videos.csv"
OUTPUT_CSV = BENCHMARK_DIR / "video_metadata.csv"

FIELDNAMES = [
    "video_key",
    "file_path",
    "description",
    "file_size_bytes",
    "file_size_mb",
    "duration_seconds",
    "duration_minutes",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect benchmark video metadata.")
    parser.add_argument("--videos-csv", type=Path, default=VIDEOS_CSV)
    parser.add_argument("--output", type=Path, default=OUTPUT_CSV)
    return parser.parse_args()


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def main() -> None:
    args = parse_args()
    rows = []
    for video in read_csv_rows(args.videos_csv):
        video_path = resolve_repo_path(video["file_path"])
        if not video_path.exists():
            rows.append({
                **video,
                "file_size_bytes": "",
                "file_size_mb": "",
                "duration_seconds": "",
                "duration_minutes": "",
            })
            continue

        rows.append({
            **video,
            **video_metadata(video_path),
        })

    write_csv_rows(args.output, FIELDNAMES, rows)

    print(f"Wrote {display_path(args.output)}")
    for row in rows:
        print(
            f"{row['video_key']}: {row['file_path']} "
            f"({row.get('file_size_mb', '')} MB, {row.get('duration_minutes', '')} min)"
        )


if __name__ == "__main__":
    main()
