# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Version = '1.0.0rc2'
$IndexUrl = ''
$Hosts = @()
$NoHosts = $false
$Uv = ''
$TempDir = $null

function Show-Help {
    Write-Host @'
Install PowerContext on Windows. Python and uv need not be installed.

Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [options]

  --version VERSION  Exact release version (default: 1.0.0rc2).
  --index-url URL    HTTPS default package index for this installation.
  --host HOST        Install an Agent integration; repeat for multiple hosts.
  --no-hosts         Install only the CLI and local Server.
  -h, --help         Show this help.

Without host options, an interactive terminal opens powercontext setup select.
Without a terminal, --host or --no-hosts is required.

Existing uv, compatible Python, and uv configuration are reused. Otherwise,
prefer Tsinghua for network country CN and PyPI elsewhere; try the other index
if the preferred one is unavailable or lacks the requested version.
--index-url disables country detection and index fallback.

Download configuration (independent of the package index):
  POWERCONTEXT_UV_INSTALLER_URL  HTTPS uv PowerShell installer URL; defaults to
                               https://astral.sh/uv/install.ps1.
  UV_INSTALLER_GITHUB_BASE_URL GitHub mirror base URL for uv binaries.
  UV_PYTHON_INSTALL_MIRROR     Mirror of Python distribution downloads.
  UV_ASTRAL_MIRROR_URL         Astral mirror for uv versions supporting it.
Existing additional uv indexes still take precedence over the default index.
'@
}

function Assert-HttpsUrl([string]$Value, [string]$Option) {
    $Uri = $null
    if (-not [Uri]::TryCreate($Value, [UriKind]::Absolute, [ref]$Uri) -or
        $Uri.Scheme -ne 'https' -or -not $Uri.Host -or $Value -match '[@?#\s]') {
        throw "$Option must be an HTTPS URL without credentials, query parameters, or fragments."
    }
}

function Save-Download([string]$Url, [string]$Path, [int]$Timeout = 30) {
    Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $Timeout -OutFile $Path
}

function Test-UvConfiguration {
    foreach ($Name in @('UV_DEFAULT_INDEX', 'UV_INDEX', 'UV_INDEX_URL', 'UV_EXTRA_INDEX_URL',
        'UV_CONFIG_FILE', 'UV_OFFLINE', 'UV_NO_INDEX', 'UV_FIND_LINKS')) {
        if ([Environment]::GetEnvironmentVariable($Name)) { return $true }
    }
    foreach ($Directory in @($env:APPDATA, $env:PROGRAMDATA)) {
        if ($Directory -and (Test-Path -LiteralPath (Join-Path $Directory 'uv\uv.toml'))) { return $true }
    }
    return $false
}

function Test-IndexVersion([string]$Url) {
    $IndexFile = Join-Path $TempDir 'index.html'
    try { Save-Download ($Url.TrimEnd('/') + '/powercontext/') $IndexFile }
    catch {
        Write-Host "Package index is unreachable: $Url"
        return $false
    }
    if ((Get-Content -LiteralPath $IndexFile -Raw) -notmatch ('powercontext-' + [regex]::Escape($Version) + '(-|\.)')) {
        Write-Host "Package index does not list PowerContext ${Version}: $Url"
        return $false
    }
    return $true
}

function Select-Index {
    if (-not $IndexUrl -and (Test-UvConfiguration)) {
        Write-Host 'Using existing uv configuration; uv will check the configured indexes.'
        return
    }
    if ($env:PIP_INDEX_URL -or $env:PIP_EXTRA_INDEX_URL) {
        Write-Host 'uv does not read pip index settings. Use --index-url or uv configuration.'
    }
    if ($IndexUrl) {
        if (-not (Test-IndexVersion $IndexUrl)) { throw 'Check the selected index or version; no fallback was selected.' }
    }
    else {
        $Country = ''
        try {
            $LocationFile = Join-Path $TempDir 'location.txt'
            Save-Download 'https://www.cloudflare.com/cdn-cgi/trace' $LocationFile 3
            if ((Get-Content -LiteralPath $LocationFile -Raw) -match '(?m)^loc=([A-Z]{2})\r?$') { $Country = $Matches[1] }
        }
        catch { Write-Host 'Network country unavailable; trying PyPI first.' }
        $Indexes = @('https://pypi.org/simple', 'https://pypi.tuna.tsinghua.edu.cn/simple')
        if ($Country -eq 'CN') {
            [array]::Reverse($Indexes)
            Write-Host 'Detected network country CN; trying the Tsinghua mirror first.'
        }
        foreach ($Candidate in $Indexes) {
            Write-Host "Checking package index: $Candidate"
            if (Test-IndexVersion $Candidate) { $script:IndexUrl = $Candidate; break }
        }
        if (-not $IndexUrl) { throw "No reachable package index lists PowerContext $Version. Use --index-url to select one." }
    }
    Write-Host "Package index: $IndexUrl"
    $env:UV_DEFAULT_INDEX = $IndexUrl
}

function Install-UvIfMissing {
    $Command = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue
    $UserUv = Join-Path $HOME '.local\bin\uv.exe'
    if ($Command) { $script:Uv = $Command.Source }
    elseif (Test-Path -LiteralPath $UserUv -PathType Leaf) { $script:Uv = $UserUv }
    else {
        Write-Host 'Installing uv in the user executable directory.'
        $Url = 'https://astral.sh/uv/install.ps1'
        if ($env:POWERCONTEXT_UV_INSTALLER_URL) { $Url = $env:POWERCONTEXT_UV_INSTALLER_URL }
        $Installer = Join-Path $TempDir 'uv-install.ps1'
        try { Save-Download $Url $Installer }
        catch { throw 'Could not download uv. Check POWERCONTEXT_UV_INSTALLER_URL; --index-url only changes Python packages.' }
        $env:UV_INSTALL_DIR = Split-Path -Parent $UserUv
        $env:UV_NO_MODIFY_PATH = '1'
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $Installer
        if ($LASTEXITCODE -ne 0) { throw 'uv installation failed. Check the installer output and UV_INSTALLER_GITHUB_BASE_URL.' }
        $script:Uv = $UserUv
    }
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) { throw 'uv executable was not found after installation.' }
    Write-Host "Using uv: $Uv"
    $env:PATH = "$(Split-Path -Parent $Uv);$env:PATH"
}

$SavedEnvironment = @{}
foreach ($Name in @('UV_DEFAULT_INDEX', 'UV_INSTALL_DIR', 'UV_NO_MODIFY_PATH')) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name)
}
try {
    for ($Index = 0; $Index -lt $args.Count; $Index++) {
        $Option = $args[$Index]
        switch ($Option) {
            { $_ -in '--version', '--index-url', '--host' } {
                $Index++
                if ($Index -ge $args.Count -or -not $args[$Index] -or $args[$Index].StartsWith('--')) {
                    throw "$Option requires a value."
                }
                switch ($Option) {
                    '--version' { $Version = $args[$Index] }
                    '--index-url' { $IndexUrl = $args[$Index] }
                    '--host' { $Hosts += @('--host', $args[$Index]) }
                }
            }
            '--no-hosts' { $NoHosts = $true }
            { $_ -in '-h', '--help' } { Show-Help; exit 0 }
            default { throw 'Unknown option. Run install.ps1 --help.' }
        }
    }
    if ($Version -notmatch '^\d+\.\d+\.\d+((a|b|rc)\d+)?$' -or $Version.StartsWith('0.0.')) {
        throw 'Use an exact package version starting at 0.1.0.'
    }
    if ($IndexUrl) { Assert-HttpsUrl $IndexUrl '--index-url' }
    if ($env:POWERCONTEXT_UV_INSTALLER_URL) { Assert-HttpsUrl $env:POWERCONTEXT_UV_INSTALLER_URL 'POWERCONTEXT_UV_INSTALLER_URL' }
    if ($NoHosts -and $Hosts.Count) { throw '--host and --no-hosts cannot be combined.' }
    if (-not $NoHosts -and -not $Hosts.Count -and (-not [Environment]::UserInteractive -or [Console]::IsInputRedirected)) {
        throw 'No interactive input. Pass --host HOST or --no-hosts.'
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Use install.sh on macOS and Linux.' }
    if (-not $NoHosts -and -not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Agent integration setup requires Git. Install Git or use --no-hosts.'
    }
    $TempDir = Join-Path ([IO.Path]::GetTempPath()) ('powercontext-install-' + [Guid]::NewGuid())
    [IO.Directory]::CreateDirectory($TempDir) | Out-Null
    Select-Index
    Install-UvIfMissing

    $Python = $null
    $FoundPython = $false
    try {
        $Python = & $Uv python find --no-project --no-python-downloads '>=3.11,<4' 2>$null
        $FoundPython = $LASTEXITCODE -eq 0 -and $Python
    }
    catch { $FoundPython = $false }
    $InstallArgs = @('tool', 'install', "powercontext[cli,server]==$Version")
    if ($FoundPython) {
        Write-Host "Using local Python: $Python"
        $InstallArgs += @('--python', $Python.Trim(), '--no-python-downloads')
    }
    else {
        Write-Host 'No compatible local Python found. uv will obtain Python 3.12.'
        $InstallArgs += @('--python', '3.12')
    }
    if ($IndexUrl) { $InstallArgs += @('--default-index', $IndexUrl) }
    & $Uv @InstallArgs
    if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Check uv indexes for packages and UV_PYTHON_INSTALL_MIRROR for Python.' }
    $ToolBin = & $Uv tool dir --bin
    if ($LASTEXITCODE -ne 0) { throw 'Could not locate the installed CLI.' }
    $Cli = Join-Path $ToolBin.Trim() 'powercontext.exe'
    $env:PATH = "$($ToolBin.Trim());$env:PATH"
    & $Cli --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'The installed CLI could not start.' }
    Write-Host "Runtime installed: $Version"
    $PathPrefix = ("$($ToolBin.Trim());$(Split-Path -Parent $Uv)").Replace("'", "''")
    Write-Host ('For a new terminal: $env:Path = ''' + $PathPrefix + ';'' + $env:Path')
    $SetupStatus = 0
    if (-not $NoHosts) {
        & $Cli setup select --source oceanbase/powercontext --ref "powercontext-v$Version" @Hosts
        $SetupStatus = $LASTEXITCODE
    }
    Write-Host "`nConfigure a generation model before starting the Server:"
    Write-Host '  https://powercontext.oceanbase.io/en/docs/get-started/configure-models/'
    Write-Host 'Then run: powercontext server run --env-file .env'
    if ($SetupStatus -ne 0) { throw "Runtime installed, but integration setup did not complete. Retry setup with powercontext-v$Version." }
}
catch {
    [Console]::Error.WriteLine("error: $($_.Exception.Message)")
    exit 1
}
finally {
    foreach ($Name in $SavedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name]) }
    if ($TempDir) { Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue }
}
