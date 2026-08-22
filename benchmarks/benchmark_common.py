from __future__ import annotations

import csv
import json
import math
import struct
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARK_DIR = Path(__file__).resolve().parent


def resolve_repo_path(path_value: str | Path) -> Path:
    path = Path(path_value)
    if path.is_absolute():
        return path
    return REPO_ROOT / path


def read_csv_rows(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def write_csv_rows(path: Path, fieldnames: list[str], rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})


def append_csv_row(path: Path, fieldnames: list[str], row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    needs_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if needs_header:
            writer.writeheader()
        writer.writerow({field: row.get(field, "") for field in fieldnames})


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")


def nested_get(payload: dict[str, Any], path: str, default: Any = "") -> Any:
    current: Any = payload
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return default
        current = current[part]
    return current


def safe_float(value: Any) -> float | None:
    if value in ("", None):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def percentile(values: list[float], quantile: float) -> float | None:
    clean_values = sorted(value for value in values if value is not None and not math.isnan(value))
    if not clean_values:
        return None
    if len(clean_values) == 1:
        return clean_values[0]

    position = (len(clean_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return clean_values[int(position)]

    lower_value = clean_values[lower]
    upper_value = clean_values[upper]
    return lower_value + (upper_value - lower_value) * (position - lower)


def mp4_duration_seconds(path: Path) -> float | None:
    size = path.stat().st_size
    with path.open("rb") as file:
        return _find_mvhd_duration(file, size)


def _find_mvhd_duration(file, end_offset: int) -> float | None:
    container_types = {b"moov", b"trak", b"mdia"}

    while file.tell() + 8 <= end_offset:
        header_start = file.tell()
        header = file.read(8)
        if len(header) < 8:
            return None

        atom_size, atom_type = struct.unpack(">I4s", header)
        header_size = 8

        if atom_size == 1:
            extended_size = file.read(8)
            if len(extended_size) < 8:
                return None
            atom_size = struct.unpack(">Q", extended_size)[0]
            header_size = 16
        elif atom_size == 0:
            atom_size = end_offset - header_start

        if atom_size < header_size:
            return None

        atom_end = header_start + atom_size
        payload_start = header_start + header_size

        if atom_type == b"mvhd":
            file.seek(payload_start)
            version_flags = file.read(4)
            if len(version_flags) < 4:
                return None
            version = version_flags[0]

            if version == 1:
                payload = file.read(28)
                if len(payload) < 28:
                    return None
                _, _, timescale, duration = struct.unpack(">QQIQ", payload)
            else:
                payload = file.read(16)
                if len(payload) < 16:
                    return None
                _, _, timescale, duration = struct.unpack(">IIII", payload)

            if timescale == 0:
                return None
            return duration / timescale

        if atom_type in container_types:
            file.seek(payload_start)
            duration = _find_mvhd_duration(file, atom_end)
            if duration is not None:
                return duration

        file.seek(atom_end)

    return None


def video_metadata(video_path: Path) -> dict[str, Any]:
    duration = mp4_duration_seconds(video_path)
    size_bytes = video_path.stat().st_size
    return {
        "file_size_bytes": size_bytes,
        "file_size_mb": round(size_bytes / (1024 * 1024), 3),
        "duration_seconds": round(duration, 3) if duration is not None else "",
        "duration_minutes": round(duration / 60, 3) if duration is not None else "",
    }
