"""Convert JSON search spans to a Chrome/Perfetto-compatible trace file."""
import argparse
import json
from pathlib import Path


def convert(lines, trace_id=None):
    events = []
    processes, threads = {}, {}
    for line in lines:
        record = json.loads(line)
        if record.get("event") != "search_span" or (trace_id and record.get("trace_id") != trace_id):
            continue
        duration = record["duration_seconds"] * 1e6
        identity = record.get("trace_id") or "uncorrelated"
        if identity not in processes:
            processes[identity] = len(processes) + 1
            events.append({"name": "process_name", "ph": "M", "pid": processes[identity], "tid": 0,
                           "args": {"name": identity}})
        threads.setdefault(record["span"], len(threads) + 1)
        events.append({"name": record["span"], "cat": "library_search", "ph": "X",
            "ts": record["timestamp"] * 1e6 - duration, "dur": duration,
            "pid": processes[identity], "tid": threads[record["span"]],
            "args": {key: record.get(key) for key in ("span_id", "parent_span_id", "status")}})
    return {"traceEvents": events, "displayTimeUnit": "ms"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--trace-id")
    args = parser.parse_args()
    args.output.write_text(json.dumps(convert(args.input.read_text(encoding="utf-8").splitlines(), args.trace_id)), encoding="utf-8")
