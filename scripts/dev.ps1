$ErrorActionPreference='Stop'
$root=Split-Path -Parent $PSScriptRoot
Set-Location $root
if (-not (Test-Path 'runtime/python/python.exe')) { throw 'Run python scripts/prepare-runtime.py first.' }
& npm.cmd run build --prefix apps/web
if ($LASTEXITCODE -ne 0) { throw 'Web build failed.' }
& npm.cmd start --prefix apps/desktop
