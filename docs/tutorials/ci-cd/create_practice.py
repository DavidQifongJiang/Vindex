"""Copy only the teaching starter into a fresh directory outside Vindex."""
import argparse
from pathlib import Path
import shutil


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    course = Path(__file__).resolve().parent
    repository = course.parents[2]
    destination = args.destination.expanduser().resolve()
    if destination == repository or repository in destination.parents:
        raise SystemExit("Choose a directory outside Vindex for your independent practice repository.")
    if destination.exists():
        raise SystemExit(f"Destination already exists; nothing changed: {destination}")
    shutil.copytree(course / "starter", destination,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    print(f"Practice files created: {destination}")
    print("Next: change into that directory and run python -m unittest discover -s tests -v")


if __name__ == "__main__":
    main()
