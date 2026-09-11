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
$UvVersion = '0.12.12'
$Region = 'auto'
if ($env:POWERCONTEXT_INSTALL_REGION) { $Region = $env:POWERCONTEXT_INSTALL_REGION }
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
  --region REGION   auto, cn, or global (default: POWERCONTEXT_INSTALL_REGION or auto).
  --index-url URL    HTTPS default package index for this installation.
  --host HOST        Install an Agent integration; repeat for multiple hosts.
  --no-hosts         Install only the CLI and local Server.
  -h, --help         Show this help.

Without host options, an interactive terminal opens powercontext setup select.
Without a terminal, --host or --no-hosts is required.

Region priority: --region, POWERCONTEXT_INSTALL_REGION, named timezone, locale
territory, then global. No network location service is queried.
CN defaults: Tsinghua for PyPI, USTC for uv, NJU for Python. Global defaults use
PyPI and Astral's download channels. Unavailable automatic mirrors fall back to
official sources; explicit download settings are preserved without fallback.

Download configuration (independent of the package index):
  POWERCONTEXT_UV_INSTALLER_URL  HTTPS uv PowerShell installer URL.
  UV_DOWNLOAD_URL              uv release artifact directory.
  UV_INSTALLER_GITHUB_BASE_URL  GitHub mirror base URL for uv binaries.
  UV_PYTHON_INSTALL_MIRROR      Mirror of Python distribution downloads.
  UV_ASTRAL_MIRROR_URL          Astral mirror for uv versions supporting it.
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

function Select-Region {
    $Source = 'explicit'
    if ($Region -eq 'auto') {
        $Source = 'timezone'
        $Timezone = [TimeZoneInfo]::Local.Id
        if ($env:TZ) { $Timezone = $env:TZ.TrimStart(':') }
        if ($Timezone -in @('Asia/Shanghai', 'Asia/Chongqing', 'Asia/Chungking', 'Asia/Harbin', 'Asia/Urumqi', 'PRC', 'China Standard Time')) {
            $script:Region = 'cn'
        }
        elseif ($Timezone -match '^(Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/' -or
            ($Timezone -ne 'UTC' -and -not $env:TZ -and $Timezone -match 'Standard Time$')) {
            $script:Region = 'global'
        }
        else {
            $Source = 'locale'
            $LocaleName = ''
            foreach ($Name in @('LC_ALL', 'LC_MESSAGES', 'LANG')) {
                $LocaleName = [Environment]::GetEnvironmentVariable($Name)
                if ($LocaleName) { break }
            }
            if (-not $LocaleName -or $LocaleName -match '^(C($|\.)|POSIX$)') { $LocaleName = [Globalization.CultureInfo]::CurrentCulture.Name }
            $script:Region = 'global'
            if ($LocaleName -match '[_-]CN($|[.@])') { $script:Region = 'cn' }
        }
    }
    Write-Host "Download region: $Region ($Source)."
}

function Test-UvConfigFile {
    if ($env:UV_CONFIG_FILE) { return $true }
    if ($env:UV_NO_CONFIG -in @('1', 'true')) { return $false }
    foreach ($Directory in @($env:APPDATA, $env:PROGRAMDATA)) {
        if ($Directory -and (Test-Path -LiteralPath (Join-Path $Directory 'uv\uv.toml'))) { return $true }
    }
    return $false
}

function Test-UvConfiguration {
    foreach ($Name in @('UV_DEFAULT_INDEX', 'UV_INDEX', 'UV_INDEX_URL', 'UV_EXTRA_INDEX_URL',
        'UV_OFFLINE', 'UV_NO_INDEX', 'UV_FIND_LINKS')) {
        if ([Environment]::GetEnvironmentVariable($Name)) { return $true }
    }
    return (Test-UvConfigFile)
}

function Test-DownloadUrl([string]$Url) {
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -Method Head -TimeoutSec 10 | Out-Null
        return $true
    }
    catch { return $false }
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
        $Indexes = @('https://pypi.org/simple', 'https://pypi.tuna.tsinghua.edu.cn/simple')
        if ($Region -eq 'cn') { [array]::Reverse($Indexes) }
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
        if ($env:UV_OFFLINE -in @('1', 'true')) { throw 'uv is not installed and offline mode disables downloads.' }
        Write-Host "Installing uv $UvVersion in the user executable directory."
        $Url = "https://astral.sh/uv/$UvVersion/install.ps1"
        if ($env:POWERCONTEXT_UV_INSTALLER_URL) { $Url = $env:POWERCONTEXT_UV_INSTALLER_URL }
        $Installer = Join-Path $TempDir 'uv-install.ps1'
        $CustomDownload = $env:POWERCONTEXT_UV_INSTALLER_URL -or $env:UV_DOWNLOAD_URL -or $env:INSTALLER_DOWNLOAD_URL -or
            $env:UV_INSTALLER_GITHUB_BASE_URL -or $env:UV_INSTALLER_GHE_BASE_URL -or $env:UV_ASTRAL_MIRROR_URL
        $Downloaded = $false
        if ($Region -eq 'cn' -and -not $CustomDownload) {
            $Mirror = "https://mirrors.ustc.edu.cn/github-release/astral-sh/uv/$UvVersion"
            # Check the actual archive before overriding the official installer's sources.
            $Architecture = $env:PROCESSOR_ARCHITECTURE
            if ($env:PROCESSOR_ARCHITEW6432) { $Architecture = $env:PROCESSOR_ARCHITEW6432 }
            $Target = switch ($Architecture) { 'AMD64' { 'x86_64' }; 'ARM64' { 'aarch64' }; 'x86' { 'i686' } }
            if ($Target -and (Test-DownloadUrl "$Mirror/uv-$Target-pc-windows-msvc.zip")) {
                try {
                    Write-Host "uv mirror: $Mirror"
                    Save-Download "$Mirror/uv-installer.ps1" $Installer
                    $Downloaded = $true
                    $env:UV_DOWNLOAD_URL = $Mirror
                }
                catch { Write-Host 'uv mirror installer unavailable; using the official installer.' }
            }
            else { Write-Host 'uv mirror archive unavailable; using official sources.' }
        }
        if (-not $Downloaded) {
            try { Save-Download $Url $Installer }
            catch { throw 'Could not download uv. Check POWERCONTEXT_UV_INSTALLER_URL.' }
        }
        $env:UV_INSTALL_DIR = Split-Path -Parent $UserUv
        $env:UV_NO_MODIFY_PATH = '1'
        & (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') -NoProfile -ExecutionPolicy Bypass -File $Installer
        if ($LASTEXITCODE -ne 0) { throw 'uv installation failed. Check the installer output and configured download source.' }
        $script:Uv = $UserUv
    }
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) { throw 'uv executable was not found after installation.' }
    Write-Host "Using uv: $Uv"
    $env:PATH = "$(Split-Path -Parent $Uv);$env:PATH"
}

$SavedEnvironment = @{}
foreach ($Name in @('UV_DEFAULT_INDEX', 'UV_INSTALL_DIR', 'UV_NO_MODIFY_PATH', 'UV_DOWNLOAD_URL')) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name)
}
try {
    for ($Index = 0; $Index -lt $args.Count; $Index++) {
        $Option = $args[$Index]
        switch ($Option) {
            { $_ -in '--version', '--region', '--index-url', '--host' } {
                $Index++
                if ($Index -ge $args.Count -or -not $args[$Index] -or $args[$Index].StartsWith('--')) {
                    throw "$Option requires a value."
                }
                switch ($Option) {
                    '--version' { $Version = $args[$Index] }
                    '--region' { $Region = $args[$Index] }
                    '--index-url' { $IndexUrl = $args[$Index] }
                    '--host' { $Hosts += @('--host', $args[$Index]) }
                }
            }
            '--no-hosts' { $NoHosts = $true }
            { $_ -in '-h', '--help' } { Show-Help; exit 0 }
            default { throw 'Unknown option. Run install.ps1 --help.' }
        }
    }
    if ($Region -cnotin @('auto', 'cn', 'global')) { throw 'Use --region auto, cn, or global.' }
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
    Select-Region
    Install-UvIfMissing

    $Python = $null
    $FoundPython = $false
    try {
        $Python = & $Uv python find --no-project --no-python-downloads '>=3.11,<4' 2>$null
        $FoundPython = $LASTEXITCODE -eq 0 -and $Python
    }
    catch { $FoundPython = $false }
    if ($FoundPython) {
        Write-Host "Using local Python: $Python"
    }
    else {
        if ($env:UV_PYTHON_DOWNLOADS -in @('never', 'false', '0')) { throw 'No compatible local Python; UV_PYTHON_DOWNLOADS disables downloads.' }
        if ($env:UV_OFFLINE -in @('1', 'true')) { throw 'No compatible local Python in offline mode.' }
        $PythonArgs = @('python', 'install', '3.12')
        if ($Region -eq 'cn' -and -not ($env:UV_PYTHON_INSTALL_MIRROR -or $env:UV_ASTRAL_MIRROR_URL -or
            $env:UV_PYTHON_DOWNLOADS_JSON_URL -or (Test-UvConfigFile))) {
            $Downloads = & $Uv python list 'cpython@3.12' --only-downloads --show-urls --color never
            if ($LASTEXITCODE -ne 0) { throw 'Could not resolve a Python download.' }
            $Url = (@($Downloads)[0] -split '\s+')[-1]
            if ($Url -match '^https://[^/]+/(?:github/|astral-sh/)?python-build-standalone/releases/download/(.+)$') {
                $Mirror = 'https://mirror.nju.edu.cn/github-release/astral-sh/python-build-standalone'
                if (Test-DownloadUrl "$Mirror/$($Matches[1])") {
                    Write-Host "Python mirror: $Mirror"
                    $PythonArgs += @('--mirror', $Mirror)
                }
                else { Write-Host 'Python mirror does not provide the requested build; using uv default sources.' }
            }
        }
        Write-Host 'No compatible local Python found. Installing Python 3.12.'
        & $Uv @PythonArgs
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed. Check uv output and UV_PYTHON_INSTALL_MIRROR.' }
        $Python = & $Uv python find --no-project --no-python-downloads 3.12
        if ($LASTEXITCODE -ne 0 -or -not $Python) { throw 'Installed Python was not found.' }
    }
    Select-Index
    $InstallArgs = @('tool', 'install', "powercontext[cli,server]==$Version", '--python', $Python.Trim(), '--no-python-downloads')
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
