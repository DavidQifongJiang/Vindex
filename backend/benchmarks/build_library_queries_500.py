"""Build/check a query-authoring set. Does not generate relevance judgments."""
import argparse
from collections import Counter
import csv
from hashlib import sha256
import io
import json
from pathlib import Path
import re
import unicodedata

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "library_queries_500"
DEV = {f"mit6006_lecture{i:02}" for i in range(1, 11)} | {
    "mit6046_lecture01", "mit6046_lecture05", "mit6046_lecture08",
    "mit6034_lecture01", "mit6034_lecture10",
}
FIELDS = ["query_id", "split", "query_type", "query", "intended_answerability",
          "authoring_video_key", "authoring_video_title", "source_page", "caption_file",
          "caption_sha256", "split_group", "judgment_status"]


def normalize(text):
    return " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", text).casefold()))


def read_blocks():
    blocks = {}
    current = None
    for raw in (ROOT / "library_queries_500_authoring.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = line[1:-1]
            if current in blocks:
                raise ValueError(f"Duplicate video block: {current}")
            blocks[current] = []
        else:
            if current is None:
                raise ValueError("Query outside a video block")
            blocks[current].append(line)
    return blocks


def csv_text(rows, fields):
    stream = io.StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue()


def build():
    manifest = json.loads((ROOT / "open_lectures_50_manifest.json").read_text(encoding="utf-8"))
    videos = manifest["existing_videos"] + manifest["new_videos"]
    captions = json.loads((ROOT / "lecture_captions" / "manifest.json").read_text(encoding="utf-8"))
    captions = {row["video_key"]: row for row in captions["captions"]}
    blocks = read_blocks()
    negatives = [line.strip() for line in (ROOT / "library_no_answer_50.txt").read_text(encoding="utf-8").splitlines()
                 if line.strip() and not line.startswith("#")]
    if len(videos) != 50 or len(negatives) != 50 or set(blocks) != {v["video_key"] for v in videos}:
        raise ValueError("Expected exactly 50 matching video blocks and 50 negative candidates")
    rows = []
    for i, video in enumerate(videos):
        key = video["video_key"]
        caption = captions[key]
        data = (ROOT.parent / caption["file_path"]).read_bytes()
        if sha256(data).hexdigest() != caption["sha256"] or not data.decode('utf-8-sig').startswith('WEBVTT'):
            raise ValueError(f"Caption integrity mismatch: {key}")
        queries = blocks[key]
        if len(queries) != 9:
            raise ValueError(f"Expected 9 queries for {key}, got {len(queries)}")
        for j, query in enumerate(queries + [negatives[i]]):
            negative = j == 9
            rows.append(dict(zip(FIELDS, [
                f"lq500_{len(rows)+1:04}", "development" if key in DEV else "reserved_evaluation",
                "no_answer_candidate" if negative else ("lexical", "paraphrase", "conceptual")[j // 3],
                query, "candidate_unanswerable" if negative else "candidate_answerable",
                "" if negative else key, "" if negative else video["title"],
                "" if negative else video["source_page"], "" if negative else caption["file_path"],
                "" if negative else caption["sha256"], key, "needs_human_review",
            ])))
    normalized = [normalize(row["query"]) for row in rows]
    if len(set(normalized)) != 500:
        raise ValueError("Duplicate normalized query text")
    old_path = ROOT / "semantic_search_labels_expanded_15min.csv"
    with old_path.open(encoding="utf-8-sig", newline="") as handle:
        old = {normalize(row["query"]) for row in csv.DictReader(handle)}
    if set(normalized) & old:
        raise ValueError("Exact query reuse from the earlier benchmark")
    development = [row for row in rows if row["split"] == "development"]
    reserved = [row for row in rows if row["split"] == "reserved_evaluation"]
    if len(development) != 150 or len(reserved) != 350:
        raise ValueError("Invalid split sizes")
    dev_groups = {row["split_group"] for row in development}
    eval_groups = {row["split_group"] for row in reserved}
    if dev_groups & eval_groups:
        raise ValueError("Video groups overlap")
    if not {f"mit6006_lecture{i:02}" for i in range(1, 11)} <= dev_groups:
        raise ValueError("Previously exposed videos must stay in development")
    report = {
        "dataset_version": "1.0-draft", "query_count": len(rows),
        "query_types": dict(Counter(row["query_type"] for row in rows)),
        "splits": {name: {"queries": len(items), "video_groups": len({r['split_group'] for r in items}),
                           "query_types": dict(Counter(r["query_type"] for r in items))}
                   for name, items in [("development", development), ("reserved_evaluation", reserved)]},
        "caption_tracks_verified": len(captions), "duplicate_normalized_queries": 0,
        "exact_queries_reused_from_prior_100": 0, "overlapping_video_groups": 0,
        "relevance_judgments_embedded_in_query_csv": 0,
        "status": "Query-authoring structure validation only; subsequent reviews and experiments are recorded separately",
        "review_status_source": "human_review_session.json",
        "experiment_status_source": "comparison_report.md",
        "limitations": [
            "Authoring video is a source hint, not an exclusive or verified relevant-video label.",
            "No-answer rows are candidates, not established corpus-wide negative judgments.",
            "Reserved queries are visible; this is not an independently authored blind test.",
            "Topic overlap exists across courses despite disjoint source-video groups.",
            "Caption grounding is at lecture level; not every query has been checked for an answer span.",
            "All 50 videos remain in the search corpus for either query split.",
        ],
    }
    files = {"queries.csv": csv_text(rows, FIELDS),
             "development_150.csv": csv_text(development, FIELDS),
             "reserved_evaluation_350.csv": csv_text(reserved, FIELDS)}
    report["artifact_sha256"] = {name: sha256(value.encode("utf-8")).hexdigest() for name, value in files.items()}
    files["validation.json"] = json.dumps(report, indent=2) + "\n"
    return files, report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Verify inputs and exact generated artifact contents")
    args = parser.parse_args()
    files, report = build()
    if args.check:
        for name, value in files.items():
            if (OUT / name).read_bytes() != value.encode("utf-8"):
                raise ValueError(f"Artifact out of date: {name}")
    else:
        OUT.mkdir(exist_ok=True)
        for name, value in files.items():
            (OUT / name).write_bytes(value.encode("utf-8"))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
