# Run the isolated reviewer from any directory; arguments are forwarded verbatim.
$reviewRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$reviewPython = Join-Path $reviewRoot 'tools/code_review/.venv/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $reviewPython)) {
    throw 'Reviewer environment missing. Run ops/setup_code_review.ps1 first.'
}
Push-Location $reviewRoot
try {
    & $reviewPython -m tools.code_review @args
    $reviewExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $reviewExitCode
