"""Isolated real Postgres/Kafka workload; synthetic events, not retrieval quality."""
import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import threading
import time
from uuid import uuid4

from sqlalchemy import create_engine, text, select, func
from sqlalchemy.orm import sessionmaker
from app.db.models import Base
from app.db.search_event_models import SearchEventBudget, SearchEventProjection, SearchEventReceipt
from app.db import search_event_repository as repo
from app.services.search_events import completion
from app.workers import search_events as worker


def projection_digest(factory, generation):
    """Bounded read of a quiescent generation; canonical bytes match replay."""
    digest, cursor, count = sha256(b"["), "", 0
    while True:
        with factory() as db:
            rows = db.execute(select(SearchEventProjection.search_id, SearchEventProjection.state)
                .where(SearchEventProjection.generation == generation, SearchEventProjection.search_id > cursor)
                .order_by(SearchEventProjection.search_id).limit(1000)).all()
        if not rows:
            break
        for identity, state in rows:
            if count:
                digest.update(b",")
            digest.update(repo.encoded({"search_id": identity, "state": state}))
            count += 1
        cursor = rows[-1][0]
    digest.update(b"]")
    return digest.hexdigest(), count


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--rate", type=int, default=100)
    parser.add_argument("--output", default="tmp/kafka-benchmark")
    parser.add_argument("--fault", choices=["consumer", "broker"])
    args = parser.parse_args()
    if args.fault and args.seconds < 120:
        parser.error("outage drills require at least120 seconds of injection")
    url = os.environ["SEARCH_EVENTS_DATABASE_URL"]
    run = "analytics_"+uuid4().hex[:12]
    os.environ["SEARCH_EVENTS_SCHEMA"] = run
    os.environ["SEARCH_EVENTS_TOPIC"] = "vindex.test."+run
    os.environ["SEARCH_EVENTS_SYNTHETIC_ONLY"] = "true"
    os.environ["SEARCH_EVENTS_CONFIGURATION"] = "synthetic-v1"
    os.environ["SEARCH_EVENTS_MEASURE_FRESHNESS"] = "true"
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=False)
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text(f'CREATE SCHEMA "{run}"'))
    factory = repo.sessions()
    Base.metadata.create_all(factory.kw["bind"])
    with factory.begin() as db:
        db.add(SearchEventBudget(name="outbox", pending_bytes=0, expired_events=0))
    worker.bootstrap()
    logs, children = [], []
    for generation in ("analytics-v1", "review-v1"):
        log = (output/(generation+".log")).open("w", encoding="utf-8")
        logs.append(log)
        children.append(subprocess.Popen([sys.executable, "-m", "app.workers.search_events", "consume", "--generation", generation],
            stdout=log, stderr=log, env=os.environ.copy(), creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
    stop = threading.Event()
    resources = []
    def sample_resources():
        while not stop.is_set():
            sample = {"at": datetime.now(timezone.utc).isoformat()}
            try:
                result = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}",
                    "vindex-analytics-kafka-1", "vindex-analytics-postgres-1"], capture_output=True, text=True, timeout=15,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                sample["containers"] = [json.loads(line) for line in result.stdout.splitlines()]
                sample["exit_code"] = result.returncode
                if result.returncode:
                    sample["error"] = result.stderr[-1000:]
            except Exception as exc:
                sample["error"] = type(exc).__name__
            resources.append(sample)
            (output/"resources.json").write_text(json.dumps(resources, indent=2), encoding="utf-8")
            stop.wait(30)
    resource_thread = threading.Thread(target=sample_resources, daemon=True)
    resource_thread.start()
    errors = []
    def publish():
        producer = worker.producer()
        while not stop.is_set():
            try:
                worker.publish_once(producer)
            except Exception as exc:
                errors.append(type(exc).__name__)
            stop.wait(.1)
    thread = threading.Thread(target=publish, daemon=True)
    thread.start()
    start, accepted, attempted, samples = time.monotonic(), 0, 0, []
    capture_errors = []
    expected = args.seconds*args.rate
    report = dict(run=run, topic=worker.topic(), expected=expected, duration_seconds=args.seconds,
        requested_rate=args.rate, python=platform.python_version(), platform=platform.platform(),
        workload="synthetic completion events via durable outbox; not real user/search traffic",
        bootstrap=worker.connection()["bootstrap.servers"])
    report["started_at"] = datetime.now(timezone.utc).isoformat()
    report["pid"] = os.getpid()
    report["logical_cpus"] = os.cpu_count()
    report["source_sha256"] = {str(path): sha256(path.read_bytes()).hexdigest() for path in
        (Path(__file__), Path(repo.__file__), Path(worker.__file__))}
    (output/"environment.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    fault_state = {"kind": args.fault, "offline": False}
    fault_done = threading.Event()
    def fault():
        observer_engine = create_engine(url, connect_args={"options": f"-c search_path={run}"})
        observer = sessionmaker(observer_engine)
        def counts():
            with observer() as db:
                return dict(db.execute(select(SearchEventReceipt.generation, func.count())
                    .group_by(SearchEventReceipt.generation)).all())
        def docker(action):
            subprocess.run(["docker", action, "vindex-analytics-kafka-1"], check=True,
                capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        stopped_broker = False
        try:
            if stop.wait(15):
                return
            fault_state["before"] = counts()
            fault_state["accepted_before"] = accepted
            fault_state["offline"] = True
            if args.fault == "consumer":
                children[0].terminate()
                children[0].wait(10)
            else:
                docker("stop")
                stopped_broker = True
            outage_start = time.monotonic()
            stop.wait(60)
            fault_state["outage_seconds"] = time.monotonic()-outage_start
            fault_state["before_restart"] = counts()
            target = accepted
            fault_state["accepted_before_restart"] = target
            restart = time.monotonic()
            if args.fault == "consumer":
                children[0] = subprocess.Popen([sys.executable, "-m", "app.workers.search_events", "consume",
                    "--generation", "analytics-v1"], stdout=logs[0], stderr=logs[0], env=os.environ.copy(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            else:
                docker("start")
                stopped_broker = False
            fault_state["offline"] = False
            while time.monotonic()-restart < 120 and not stop.is_set():
                current = counts()
                if all(current.get(g, 0) >= target for g in ("analytics-v1", "review-v1")):
                    fault_state["catchup_seconds"] = time.monotonic()-restart
                    fault_state["catchup_counts"] = current
                    break
                stop.wait(.2)
            if args.fault == "consumer":
                fault_state["other_group_continued"] = (
                    fault_state["before_restart"].get("review-v1", 0) > fault_state["before"].get("review-v1", 0)
                    and fault_state["before_restart"].get("review-v1", 0) >= target-200)
            fault_state["pass"] = (fault_state["outage_seconds"] >= 60 and
                fault_state.get("catchup_seconds", 121) <= 120 and fault_state.get("other_group_continued", True))
        except Exception as exc:
            fault_state["failure"] = type(exc).__name__+": "+str(exc)
        finally:
            if stopped_broker:
                docker("start")
            observer_engine.dispose()
            fault_done.set()
    fault_thread = threading.Thread(target=fault, daemon=True) if args.fault else None
    if fault_thread:
        fault_thread.start()
    try:
        while attempted < expected:
            if any(p.poll() is not None for i, p in enumerate(children)
                if not (i == 0 and args.fault == "consumer" and fault_state["offline"])):
                raise RuntimeError("consumer exited; inspect logs")
            # 10 ms pacing by small batches, including real acceptance transactions.
            due = min(expected, int((time.monotonic()-start)*args.rate)+1)
            while attempted < due:
                event = completion(str(uuid4()), "semantic" if accepted % 2 else "lexical", 200,
                    {"results": [], "effective_mode": "semantic", "coverage": {"complete": True, "total_videos": 1}}, 10)
                try:
                    repo.capture_search("synthetic", "synthetic example", event, [], True)
                    accepted += 1
                except Exception as exc:
                    capture_errors.append(type(exc).__name__)
                attempted += 1
            if accepted and accepted % (args.rate*10) < 3:
                with factory() as db:
                    latest = db.execute(select(SearchEventProjection).where(SearchEventProjection.generation == "analytics-v1")
                        .order_by(SearchEventProjection.updated_at.desc()).limit(1)).scalar_one_or_none()
                    if latest:
                        samples.append((datetime.now(timezone.utc)-datetime.fromisoformat(latest.state["completion"]["occurred_at"])).total_seconds())
            time.sleep(.002)
        deadline = time.monotonic()+120
        report["injection_seconds"] = time.monotonic()-start
        counts = {}
        while time.monotonic() < deadline:
            with factory() as db:
                counts = dict(db.execute(select(SearchEventReceipt.generation, func.count()).group_by(SearchEventReceipt.generation)).all())
            if all(counts.get(g) == accepted for g in ("analytics-v1", "review-v1")):
                break
            time.sleep(.5)
        report.update(accepted=accepted, attempted=attempted, capture_errors=capture_errors, receipts=counts, elapsed_seconds=time.monotonic()-start,
            publisher_errors=errors, sampled_latest_event_age_seconds=samples,
            accounting_pass=all(counts.get(g) == accepted for g in ("analytics-v1", "review-v1")))
        if fault_thread:
            fault_done.wait(120)
            report["fault"] = fault_state.copy()
        stop.set()
        thread.join(15)
        report["projection_freshness_p95_seconds_by_group"] = {}
        for generation in ("analytics-v1", "review-v1"):
            with factory() as db:
                db.execute(text("SET LOCAL statement_timeout = '15s'"))
                value = db.scalar(text("""SELECT percentile_cont(.95) WITHIN GROUP
                    (ORDER BY EXTRACT(EPOCH FROM (updated_at AT TIME ZONE 'UTC' -
                    (state->'completion'->>'occurred_at')::timestamptz)))
                    FROM search_event_projections WHERE generation=:generation"""), {"generation": generation})
            report["projection_freshness_p95_seconds_by_group"][generation] = value
        report["admission_rate"] = accepted/report["injection_seconds"]
        with factory() as db:
            report["schema_disk_bytes"] = db.scalar(text("""SELECT coalesce(sum(pg_total_relation_size(c.oid)),0)
                FROM pg_class c JOIN pg_namespace n ON c.relnamespace=n.oid
                WHERE n.nspname=:schema AND c.relkind='r'"""), {"schema": run})
            report["schema_disk_bytes"] = int(report["schema_disk_bytes"])
        report["capture_pass"] = accepted == attempted
        report["freshness_pass"] = all(v is not None and v <= 5 for v in
            report["projection_freshness_p95_seconds_by_group"].values())
        (output/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        if not report["accounting_pass"]:
            raise RuntimeError("live_catchup_failed; replay not started")
        # Offline replay only after live groups have caught up.
        replay_started = time.monotonic()
        first = worker.replay(output/"replay.json")
        report["replay_seconds"] = time.monotonic()-replay_started
        report["live_projection_sha256"], report["live_searches"] = projection_digest(factory, "analytics-v1")
        report["replay_equal"] = report["live_projection_sha256"] == first["projection_sha256"]
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)}
        raise
    finally:
        stop.set()
        if fault_thread:
            fault_thread.join(40)
            report["fault"] = fault_state.copy()
        thread.join(15)
        for child in children:
            child.terminate()
            child.wait(10)
        for log in logs:
            log.close()
        resource_thread.join(16)
        report["committed_freshness"] = {}
        for generation in ("analytics-v1", "review-v1"):
            delays, transactions = [], []
            for line in (output/(generation+".log")).read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("event") == "batch_committed":
                    delays.extend(row["freshness_seconds"])
                    transactions.append(row["db_ms"])
            delays.sort()
            transactions.sort()
            report["committed_freshness"][generation] = {"observations": len(delays),
                "p95_seconds": delays[min(len(delays)-1, int(len(delays)*.95))] if delays else None,
                "batch_db_p95_ms": transactions[min(len(transactions)-1, int(len(transactions)*.95))] if transactions else None}
        report["freshness_pass"] = all(v["observations"] >= accepted and v["p95_seconds"] is not None and
            v["p95_seconds"] <= 5 for v in report["committed_freshness"].values())
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        (output/"report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "sampled_latest_event_age_seconds"}, indent=2))


if __name__ == "__main__":
    main()
