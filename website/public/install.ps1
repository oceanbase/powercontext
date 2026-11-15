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

$Version = 'latest'
$UvVersion = '0.12.23'
$RuntimeProfile = 'local'
$Region = 'auto'
if ($env:POWERCONTEXT_INSTALL_REGION) { $Region = $env:POWERCONTEXT_INSTALL_REGION }
$IndexUrl = ''
$Hosts = @()
$NoHosts = $false
$EnvFile = ''
$Uv = ''
$TempDir = $null

function Show-Help {
    Write-Host @'
Install PowerContext on Windows. Python and uv need not be installed.

Usage: powershell -ExecutionPolicy Bypass -File install.ps1 [options]

  --version VERSION  Exact release version, or latest (default: latest stable).
  --profile PROFILE local (CLI and Server, default) or client (CLI and Client).
  --region REGION   auto, cn, or global (default: POWERCONTEXT_INSTALL_REGION or auto).
  --index-url URL    HTTPS default package index for this installation.
  --host HOST        Install an Agent integration; repeat for multiple hosts.
  --no-hosts         Compatibility option; integration setup is skipped by default.
  --env-file PATH   Existing configuration file for selected Agent integrations.
  -h, --help         Show this help.

Agent integration setup runs only for explicitly selected --host values.
Configure and register a personal service separately after installation.
Windows personal services remain experimental.

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

function Save-Download([string]$Url, [string]$Path = '', [int]$Timeout = 30, [switch]$Head) {
    $Uri = [Uri]$Url
    for ($Redirects = 0; $Redirects -le 5; $Redirects++) {
        if ($Uri.Scheme -ne 'https' -or -not $Uri.Host -or $Uri.UserInfo) {
            throw 'Downloads and redirects must use HTTPS without embedded credentials.'
        }
        # Windows PowerShell 5.1 follows HTTPS-to-HTTP redirects by default.
        $Request = [Net.HttpWebRequest]::CreateHttp($Uri)
        $Request.AllowAutoRedirect = $false
        $Request.Timeout = $Timeout * 1000
        $Request.ReadWriteTimeout = $Timeout * 1000
        if ($Head) { $Request.Method = 'HEAD' }
        $Response = $Request.GetResponse()
        try {
            if ([int]$Response.StatusCode -in @(301, 302, 303, 307, 308)) {
                $Location = $Response.Headers['Location']
                if (-not $Location -or $Redirects -eq 5) { throw 'Invalid or excessive download redirects.' }
                $Uri = [Uri]::new($Uri, $Location)
                continue
            }
            if (-not $Head) {
                $Output = [IO.File]::Create($Path)
                try { $Response.GetResponseStream().CopyTo($Output) }
                finally { $Output.Dispose() }
            }
            return
        }
        finally { $Response.Dispose() }
    }
}

function Select-Region {
    $Source = 'explicit'
    if ($Region -eq 'auto') {
        $Source = 'timezone'
        $Timezone = [TimeZoneInfo]::Local.Id
        if ($env:TZ) { $Timezone = $env:TZ.TrimStart(':') }
        if ($Timezone -in @('Asia/Shanghai', 'Asia/Chongqing', 'Asia/Chungking', 'Asia/Harbin', 'Asia/Urumqi', 'PRC', 'China Standard Time')) {
            $Region = 'cn'
        }
        elseif ($Timezone -match '^(Africa|America|Antarctica|Arctic|Asia|Atlantic|Australia|Europe|Indian|Pacific)/' -or
            ($Timezone -ne 'UTC' -and -not $env:TZ -and $Timezone -match 'Standard Time$')) {
            $Region = 'global'
        }
        else {
            $Source = 'locale'
            $LocaleName = ''
            foreach ($Name in @('LC_ALL', 'LC_MESSAGES', 'LANG')) {
                $LocaleName = [Environment]::GetEnvironmentVariable($Name)
                if ($LocaleName) { break }
            }
            if (-not $LocaleName -or $LocaleName -match '^(C($|\.)|POSIX$)') { $LocaleName = [Globalization.CultureInfo]::CurrentCulture.Name }
            $Region = 'global'
            if ($LocaleName -match '[_-]CN($|[.@])') { $Region = 'cn' }
        }
    }
    Write-Host "Download region: $Region ($Source)."
    return $Region
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
        Save-Download $Url -Timeout 10 -Head
        return $true
    }
    catch { return $false }
}

function Test-IndexAvailable([string]$Url) {
    $IndexFile = Join-Path $TempDir 'index.html'
    # Probe transport availability only; uv interprets the Simple API and versions.
    try { Save-Download ($Url.TrimEnd('/') + '/powercontext/') $IndexFile }
    catch {
        Write-Host "Package mirror is unreachable: $Url"
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
    if (-not $IndexUrl) {
        $IndexUrl = 'https://pypi.org/simple'
        if ($Region -eq 'cn') {
            $Mirror = 'https://pypi.tuna.tsinghua.edu.cn/simple'
            Write-Host "Checking package mirror availability: $Mirror"
            if (Test-IndexAvailable $Mirror) { $IndexUrl = $Mirror }
            else { Write-Host 'Automatic package mirror unavailable; using PyPI.' }
        }
    }
    # A reachable mirror may be stale or incompatible. Let uv report that failure
    # without retrying a tool installation or changing the requested requirement.
    Write-Host "Package index: $IndexUrl"
    $env:UV_DEFAULT_INDEX = $IndexUrl
    return $IndexUrl
}

function Install-UvIfMissing {
    $Command = Get-Command uv -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $UserUv = Join-Path $HOME '.local\bin\uv.exe'
    if ($env:UV_INSTALL_DIR) { $UserUv = Join-Path $env:UV_INSTALL_DIR 'uv.exe' }
    if ($Command) { $Uv = $Command.Source }
    elseif (Test-Path -LiteralPath $UserUv -PathType Leaf) { $Uv = $UserUv }
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
            try {
                Write-Host "uv mirror: $Mirror"
                Save-Download "$Mirror/uv-installer.ps1" $Installer
                $Downloaded = $true
                $env:UV_DOWNLOAD_URL = $Mirror
            }
            catch { Write-Host 'uv mirror installer unavailable; using the official installer.' }
        }
        if (-not $Downloaded) {
            try { Save-Download $Url $Installer }
            catch { throw 'Could not download uv. Check POWERCONTEXT_UV_INSTALLER_URL.' }
        }
        $env:UV_INSTALL_DIR = Split-Path -Parent $UserUv
        $env:UV_NO_MODIFY_PATH = '1'
        $PowerShell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $Installer | Out-Host
        if ($LASTEXITCODE -ne 0 -and $Downloaded) {
            # The official PowerShell installer accepts only one UV_DOWNLOAD_URL.
            Write-Host 'uv mirror installation failed; retrying uv with official sources.'
            Remove-Item Env:UV_DOWNLOAD_URL
            & $PowerShell -NoProfile -ExecutionPolicy Bypass -File $Installer | Out-Host
        }
        if ($LASTEXITCODE -ne 0) { throw 'uv installation failed. Check the installer output and configured download source.' }
        $Uv = $UserUv
    }
    if (-not (Test-Path -LiteralPath $Uv -PathType Leaf)) { throw 'uv executable was not found after installation.' }
    Write-Host "Using uv: $Uv"
    $env:PATH = "$(Split-Path -Parent $Uv);$env:PATH"
    return $Uv
}

$SavedOutputEncoding = [Console]::OutputEncoding
$SavedEnvironment = @{}
foreach ($Name in @('UV_DEFAULT_INDEX', 'UV_INSTALL_DIR', 'UV_NO_MODIFY_PATH', 'UV_DOWNLOAD_URL')) {
    $SavedEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name)
}
try {
    [Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
    for ($Index = 0; $Index -lt $args.Count; $Index++) {
        $Option = $args[$Index]
        switch ($Option) {
            { $_ -in '--version', '--profile', '--region', '--index-url', '--host', '--env-file' } {
                $Index++
                if ($Index -ge $args.Count -or -not $args[$Index] -or $args[$Index].StartsWith('--')) {
                    throw "$Option requires a value."
                }
                switch ($Option) {
                    '--version' { $Version = $args[$Index] }
                    '--profile' { $RuntimeProfile = $args[$Index] }
                    '--region' { $Region = $args[$Index] }
                    '--index-url' { $IndexUrl = $args[$Index] }
                    '--host' { $Hosts += @($args[$Index]) }
                    '--env-file' { $EnvFile = $args[$Index] }
                }
            }
            '--no-hosts' { $NoHosts = $true }
            { $_ -in '-h', '--help' } { Show-Help; exit 0 }
            default { throw 'Unknown option. Run install.ps1 --help.' }
        }
    }
    if ($Region -cnotin @('auto', 'cn', 'global')) { throw 'Use --region auto, cn, or global.' }
    if ($RuntimeProfile -cnotin @('local', 'client')) { throw 'Use --profile local or client.' }
    if (($Version -cne 'latest' -and $Version -notmatch '^\d+\.\d+\.\d+((a|b|rc)\d+)?$') -or $Version.StartsWith('0.0.')) {
        throw 'Use latest or an exact package version starting at 0.1.0.'
    }
    if ($IndexUrl) { Assert-HttpsUrl $IndexUrl '--index-url' }
    if ($env:POWERCONTEXT_UV_INSTALLER_URL) { Assert-HttpsUrl $env:POWERCONTEXT_UV_INSTALLER_URL 'POWERCONTEXT_UV_INSTALLER_URL' }
    if ($NoHosts -and $Hosts.Count) { throw '--host and --no-hosts cannot be combined.' }
    if ($EnvFile -and -not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
        throw '--env-file must name an existing file.'
    }
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) { throw 'Use install.sh on macOS and Linux.' }
    if ($Hosts.Count -and -not (Get-Command git -CommandType Application -ErrorAction SilentlyContinue)) {
        throw 'Agent integration setup requires Git. Install Git or use --no-hosts.'
    }
    $TempDir = Join-Path ([IO.Path]::GetTempPath()) ('powercontext-install-' + [Guid]::NewGuid())
    [IO.Directory]::CreateDirectory($TempDir) | Out-Null
    $Region = Select-Region
    $Uv = Install-UvIfMissing

    $Python = $null
    $FoundPython = $false
    try {
        $Python = & $Uv python find --system --no-project --no-python-downloads '>=3.11,<4' 2>$null
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
        $AutomaticMirror = $false
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
                    $AutomaticMirror = $true
                }
                else { Write-Host 'Python mirror does not provide the requested build; using uv default sources.' }
            }
        }
        Write-Host 'No compatible local Python found. Installing Python 3.12.'
        & $Uv @PythonArgs
        if ($LASTEXITCODE -ne 0 -and $AutomaticMirror) {
            Write-Host 'Python mirror installation failed; retrying with uv default sources.'
            & $Uv python install 3.12
        }
        if ($LASTEXITCODE -ne 0) { throw 'Python installation failed. Check uv output and UV_PYTHON_INSTALL_MIRROR.' }
        $Python = & $Uv python find --system --no-project --no-python-downloads 3.12
        if ($LASTEXITCODE -ne 0 -or -not $Python) { throw 'Installed Python was not found.' }
    }
    $IndexUrl = Select-Index
    $Requirement = 'powercontext[cli,server]'
    if ($RuntimeProfile -eq 'client') { $Requirement = 'powercontext[cli]' }
    $InstallArgs = @('tool', 'install', '--python', $Python.Trim(), '--no-python-downloads')
    # Older uv retains installed prereleases unless it resolves a fresh tool environment.
    if ($Version -eq 'latest') { $InstallArgs += @('--upgrade', '--force', '--prerelease', 'disallow') }
    else { $Requirement += "==$Version" }
    if ($Hosts.Count -and $RuntimeProfile -eq 'client') {
        # Verify the selected client package supports setup before replacing the installed tool.
        $ProbeArgs = @('tool', 'run', '--isolated', '--python', $Python.Trim(), '--no-python-downloads', '--from', $Requirement)
        if ($Version -eq 'latest') { $ProbeArgs += @('--prerelease', 'disallow') }
        if ($IndexUrl) { $ProbeArgs += @('--default-index', $IndexUrl) }
        & $Uv @ProbeArgs powercontext setup --help | Out-Null
        if ($LASTEXITCODE -ne 0) {
            throw 'Client integration setup preflight failed. Check uv output, use --profile local, or omit --host.'
        }
    }
    $InstallArgs += $Requirement
    if ($IndexUrl) { $InstallArgs += @('--default-index', $IndexUrl) }
    & $Uv @InstallArgs
    if ($LASTEXITCODE -ne 0) { throw 'Installation failed. Check uv indexes for packages and UV_PYTHON_INSTALL_MIRROR for Python.' }
    $ToolBin = & $Uv tool dir --bin
    if ($LASTEXITCODE -ne 0) { throw 'Could not locate the installed CLI.' }
    $Cli = Join-Path $ToolBin.Trim() 'powercontext.exe'
    $env:PATH = "$($ToolBin.Trim());$env:PATH"
    $InstalledVersion = & $Cli --version
    if ($LASTEXITCODE -ne 0) { throw 'Package installed, but CLI version verification failed. Check uv output and retry installation.' }
    if ($InstalledVersion -notmatch '^\d+\.\d+\.\d+((a|b|rc)\d+)?$') { throw 'Package installed, but CLI release version verification failed. Check uv output and retry installation.' }
    if ($Version -ne 'latest' -and $Version -ne $InstalledVersion) { throw 'Package installed, but the CLI version differs from the requested release. Retry installation with the intended version.' }
    # Help checks import and command availability without configuring or starting a service.
    & $Cli --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Package installed, but CLI help verification failed. Retry installation after reviewing the command error.' }
    & $Cli capabilities --help | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Package installed, but Client command verification failed (capabilities --help). Retry installation after reviewing the command error.' }
    if ($RuntimeProfile -eq 'local') {
        & $Cli config init --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Package installed, but configuration command verification failed (config init --help). Retry installation after reviewing the command error.' }
        & $Cli server run --help | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Package installed, but Server command verification failed (server run --help). Retry installation after reviewing the command error.' }
    }
    $Version = $InstalledVersion.Trim()
    Write-Host "Runtime installed: $Version ($RuntimeProfile)"
    $PathPrefix = ("$($ToolBin.Trim());$(Split-Path -Parent $Uv)").Replace("'", "''")
    Write-Host ('For a new terminal: $env:Path = ''' + $PathPrefix + ';'' + $env:Path')
    $SetupStatus = 0
    if ($Hosts.Count) {
        $SetupArgs = @('setup')
        if ($EnvFile) { $SetupArgs += @('--env-file', $EnvFile) }
        foreach ($Agent in $Hosts) {
            & $Cli @SetupArgs $Agent --source oceanbase/powercontext --ref "powercontext-v$Version"
            if ($LASTEXITCODE -ne 0) { $SetupStatus = $LASTEXITCODE }
        }
    }
    if ($RuntimeProfile -eq 'local') {
        Write-Host "`nConfigure a local Server: powercontext config init --output .env"
        Write-Host 'After saving, validate the file and register the personal service:'
        Write-Host '  powercontext config validate --env-file .env'
        Write-Host '  powercontext service install --env-file .env'
        Write-Host '  powercontext service status'
        Write-Host 'Load the generated Client settings, then run powercontext ready and powercontext capabilities.'
        Write-Host 'Windows personal services are experimental. For development or temporary use, run powercontext server run.'
        Write-Host '  https://powercontext.oceanbase.io/en/docs/get-started/quickstart/'
    }
    else {
        Write-Host "`nConnect to your existing Server:"
        Write-Host '  https://powercontext.oceanbase.io/en/docs/operate/connect-remote-server/'
    }
    if ($SetupStatus -ne 0) { throw "Runtime installed, but integration setup did not complete. Retry setup with powercontext-v$Version." }
}
catch {
    [Console]::Error.WriteLine("error: $($_.Exception.Message)")
    exit 1
}
finally {
    [Console]::OutputEncoding = $SavedOutputEncoding
    foreach ($Name in $SavedEnvironment.Keys) { [Environment]::SetEnvironmentVariable($Name, $SavedEnvironment[$Name]) }
    if ($TempDir) { Remove-Item -LiteralPath $TempDir -Recurse -Force -ErrorAction SilentlyContinue }
}
