# CI/CD practice app

A disposable video chunk planner for the Vindex CI/CD tutorial. Python 3.11 or newer is enough for the first labs. No Python packages, database, AWS account, or credentials are required.

Run `python -m unittest discover -s tests -v`, then `python app.py`.
Open `http://127.0.0.1:18080/plan?seconds=301`: the answer is two chunks.

The complete instructions are in the accompanying tutorial, one directory above this starter. Copy this starter into its own directory outside Vindex before initializing Git. Workflow examples under `solutions/` are inactive until you copy one to `.github/workflows/ci.yml` in your practice repository.

This app teaches release mechanics. Its standard-library HTTP server and fault-injection flags are for practice, not a public production service.
