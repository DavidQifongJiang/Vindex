$ErrorActionPreference = 'Stop'
$reviewRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$env:UV_CACHE_DIR = Join-Path $reviewRoot '.review-cache/uv'
$env:UV_PYTHON_INSTALL_DIR = Join-Path $reviewRoot '.review-cache/python'
Push-Location $reviewRoot
try {
    if (-not (Test-Path -LiteralPath 'tools/code_review/.venv/Scripts/python.exe')) {
        uv venv --python 3.11 tools/code_review/.venv
        if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.11 environment.' }
    }
    uv pip sync --require-hashes tools/code_review/requirements.lock --python tools/code_review/.venv/Scripts/python.exe
    if ($LASTEXITCODE -ne 0) { throw 'Could not install reviewer dependencies.' }
    & tools/code_review/.venv/Scripts/python.exe -m tools.code_review doctor
    if ($LASTEXITCODE -ne 0) { throw 'Sign in using codex login with ChatGPT, then rerun doctor.' }
} finally {
    Pop-Location
}
