#!/usr/bin/env bash
# Build the frontend per feature configuration and run every browser suite against production builds.
# All API and media traffic is fixtures: this verifies UI behavior, not Cognito or cloud processing.
# Run from the repository root after `npm ci` and `npx playwright install chromium` in frontend/.
set -uo pipefail

root="$(pwd)"
output="${VINDEX_BROWSER_OUTPUT:-$root/.review-cache/ci-browser}"
export PLAYWRIGHT_MODULE="$root/frontend/node_modules/playwright"
rm -rf "$output"
mkdir -p "$output"
servers=()
trap 'for pid in "${servers[@]:-}"; do kill "$pid" 2>/dev/null; done' EXIT

build_and_serve() {
  local name="$1" port="$2"
  shift 2
  (cd frontend && env "$@" npx vite build --outDir "dist-ci-$name" --emptyOutDir) || exit 1
  (cd frontend && npx vite preview --outDir "dist-ci-$name" --host 127.0.0.1 --port "$port" --strictPort) \
    > "$output/preview-$name.log" 2>&1 &
  servers+=("$!")
  for _ in $(seq 1 30); do
    curl -fs "http://127.0.0.1:$port/" > /dev/null && return 0
    sleep 1
  done
  echo "Preview server for $name did not start" >&2
  exit 1
}

# Keep going after a failing suite so each one reports; check_results decides the outcome.
run_suite() {
  local name="$1"
  shift
  echo "=== $name"
  env VINDEX_VERIFY_OUTPUT="$output/$name" "$@" || echo "$name exited non-zero"
}

build_and_serve default 5184
build_and_serve search 5185 VITE_LIBRARY_SEARCH_ENABLED=true VITE_SEARCH_ANALYTICS_ENABLED=true
build_and_serve auth 5186 VITE_AUTH_ENABLED=true VITE_COGNITO_DOMAIN=https://auth.invalid \
  VITE_COGNITO_CLIENT_ID=fixture-client VITE_COGNITO_REDIRECT_URI=http://127.0.0.1:5186/

run_suite library-default VINDEX_UI_URL=http://127.0.0.1:5184 VINDEX_EXPECT_SEARCH_DISABLED=true \
  node backend/tests/browser/library_search_browser.cjs
run_suite library VINDEX_UI_URL=http://127.0.0.1:5185 node backend/tests/browser/library_search_browser.cjs
run_suite frontend VINDEX_UI_URL=http://127.0.0.1:5185 VINDEX_AUTH_UI_URL=http://127.0.0.1:5186 \
  node backend/tests/browser/frontend_verification.cjs

node backend/tests/browser/check_results.cjs \
  "$output/library-default" "$output/library" "$output/analytics" "$output/frontend"
