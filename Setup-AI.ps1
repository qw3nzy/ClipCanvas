param([string]$PythonPath)
$ErrorActionPreference = 'Stop'
$rootPath = $PSScriptRoot
if (-not $PythonPath) {
    $recordPath = Join-Path $rootPath '.runtime\engine.pid'
    if (Test-Path -LiteralPath $recordPath) {
        $PythonPath = (Get-Content -LiteralPath $recordPath -Raw | ConvertFrom-Json).python_path
    }
    if (-not $PythonPath) { $PythonPath = (Get-Command python -ErrorAction Stop).Source }
}
& $PythonPath -B (Join-Path $rootPath 'engine\install_ai.py')
if ($LASTEXITCODE -ne 0) { throw 'Local AI installation failed.' }
