$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    throw 'Project Python not found. Follow README.md to install dependencies.'
}
if (-not (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'dist\index.html'))) {
    throw 'Frontend build not found. Run npm install and npm run build first.'
}
Write-Host 'EvidenceMap: http://127.0.0.1:8000 (Ctrl+C to stop)'
& $projectPython -m uvicorn backend.app:app --host 127.0.0.1 --port 8000
