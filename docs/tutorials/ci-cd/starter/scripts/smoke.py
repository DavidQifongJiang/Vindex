"""Check readiness, behavior and optionally the exact deployed release."""
import argparse
import json
import time
from urllib.request import urlopen


def check(base_url, expected_release=None):
    def get(path):
        with urlopen(base_url.rstrip("/") + path, timeout=2) as response:
            return json.load(response)

    ready = get("/ready")
    if ready.get("status") != "ready":
        raise ValueError("readiness check did not return ready")
    if expected_release and ready.get("release") != expected_release:
        raise ValueError(f"wrong release: expected {expected_release}, got {ready.get('release')}")
    plan = get("/plan?seconds=301")
    if plan.get("chunks") != 2:
        raise ValueError("behavior check failed: 301 seconds must produce 2 chunks")
    if expected_release and plan.get("release") != expected_release:
        raise ValueError("plan endpoint returned a different release")
    return ready


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("base_url")
    parser.add_argument("--expected-release")
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    deadline = time.monotonic() + max(0, args.timeout)
    while True:
        try:
            result = check(args.base_url, args.expected_release)
        except (OSError, ValueError) as error:
            if time.monotonic() >= deadline:
                raise SystemExit(f"SMOKE FAILED: {error}") from error
            time.sleep(0.25)
        else:
            print("SMOKE PASSED: " + json.dumps(result))
            return


if __name__ == "__main__":
    main()
