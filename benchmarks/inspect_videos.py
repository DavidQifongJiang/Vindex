from __future__ import annotations

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


def main() -> None:
    rows = []
    for video in read_csv_rows(VIDEOS_CSV):
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

    write_csv_rows(OUTPUT_CSV, FIELDNAMES, rows)

    print(f"Wrote {OUTPUT_CSV.relative_to(Path.cwd())}")
    for row in rows:
        print(
            f"{row['video_key']}: {row['file_path']} "
            f"({row.get('file_size_mb', '')} MB, {row.get('duration_minutes', '')} min)"
        )


if __name__ == "__main__":
    main()
