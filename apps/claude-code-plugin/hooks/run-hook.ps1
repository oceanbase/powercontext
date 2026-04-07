# PowerShell launcher for Windows (native Claude Code without Git Bash).
# Merge hooks command into settings — see hooks/hooks.windows.example.json
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$arch = if ($env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { 'arm64' } else { 'amd64' }
$exe = Join-Path $Root "bin\powermem-hook-windows-$arch.exe"
if (-not (Test-Path $exe)) {
    $exe = Join-Path $Root "bin\powermem-hook-windows-amd64.exe"
}
if (-not (Test-Path $exe)) {
    exit 0
}
& $exe @args
exit $LASTEXITCODE
