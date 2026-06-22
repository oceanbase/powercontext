# PowerShell launcher for Windows (native Claude Code without Git Bash).
# Merge hooks command into settings — see hooks/hooks.windows.example.json
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$PluginRoot = Split-Path -Parent $Root

function Import-PowerMemEnvFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed -notmatch '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)\s*$') {
            continue
        }
        $name = $Matches[1]
        $value = $Matches[2].Trim()
        if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

$dataDir = if ($env:POWERMEM_DATA_DIR) { $env:POWERMEM_DATA_DIR } else { Join-Path $HOME ".powermem" }
Import-PowerMemEnvFile (Join-Path $dataDir "runtime.env")
Import-PowerMemEnvFile (Join-Path $PluginRoot "config\runtime.env")

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
