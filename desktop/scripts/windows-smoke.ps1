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

# CI only: this does not qualify interactive or standard-user behavior.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Run on a disposable GitHub Actions runner.' }
$desktopRoot = Split-Path -Parent $PSScriptRoot
$packages = @(Get-ChildItem -LiteralPath "$desktopRoot/src-tauri/target/release/bundle/nsis" -Filter '*-setup.exe')
if ($packages.Count -ne 1) { throw 'Expected exactly one installer.' }
$package = $packages[0]
$testRoot = Join-Path $env:RUNNER_TEMP "desktop-smoke-$([guid]::NewGuid().ToString('N'))"
$installDir = Join-Path $testRoot '中文安装目录'
$sentinel = Join-Path $testRoot 'external-data.txt'
New-Item -ItemType Directory -Path $testRoot | Out-Null
Set-Content -LiteralPath $sentinel -Value 'Synthetic external data; not a Server database.' -Encoding utf8
$sentinelHash = (Get-FileHash -LiteralPath $sentinel).Hash
$signature = Get-AuthenticodeSignature -LiteralPath $package.FullName
$os = Get-CimInstance Win32_OperatingSystem
$report = [ordered]@{
    scope = 'Hosted runner; not standard-user, absent-WebView2 or visual UI qualification'
    runnerImage = $env:ImageVersion
    commit = $env:GITHUB_SHA
    sourceInstallerCommit = $(if ($env:DESKTOP_INSTALLER_COMMIT) { $env:DESKTOP_INSTALLER_COMMIT } else { $env:GITHUB_SHA })
    measuredAtUtc = [DateTime]::UtcNow.ToString('o')
    os = "$($os.Caption) $($os.Version) $($os.OSArchitecture)"
    buildProfile = 'release'
    installerBytes = $package.Length
    installerSha256 = (Get-FileHash -LiteralPath $package.FullName).Hash
    signature = $signature.Status.ToString()
    signerSubject = if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null }
    installedExecutableSha256 = $null
    installedExecutableBytes = $null
    installExit = $null
    uninstallExit = $null
    externalSentinelPreserved = $false
}
try {
    # NSIS /D must be last and unquoted, including paths with spaces.
    $installer = Start-Process -FilePath $package.FullName -ArgumentList "/S /D=$installDir" -PassThru -Wait -WindowStyle Hidden
    $report.installExit = $installer.ExitCode
    if ($installer.ExitCode -ne 0) { throw 'Installer failed.' }
    if (-not (Test-Path -LiteralPath (Join-Path $installDir 'powercontext-desktop.exe'))) {
        throw 'Installed executable missing.'
    }
    $installed = Get-Item -LiteralPath (Join-Path $installDir 'powercontext-desktop.exe')
    $report.installedExecutableSha256 = (Get-FileHash -LiteralPath $installed.FullName).Hash
    $report.installedExecutableBytes = $installed.Length
    uv run --no-sync python "$desktopRoot/tests/installed_ui.py" $installed.FullName
    if ($LASTEXITCODE -ne 0) { throw 'Installed native UI check failed.' }
} finally {
    try {
        # Only the exact task-owned installed program may be cleaned up after a failed UI run.
        $ownedExecutable = Join-Path $installDir 'powercontext-desktop.exe'
        Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -eq $ownedExecutable } | ForEach-Object { Stop-Process -Id $_.ProcessId -ErrorAction SilentlyContinue }
        $uninstaller = Join-Path $installDir 'uninstall.exe'
        if (Test-Path -LiteralPath $uninstaller) {
            $uninstall = Start-Process -FilePath $uninstaller -ArgumentList "/S _?=$installDir" -PassThru -Wait -WindowStyle Hidden
            $report.uninstallExit = $uninstall.ExitCode
            if ($uninstall.ExitCode -ne 0 -or (Test-Path -LiteralPath (Join-Path $installDir 'powercontext-desktop.exe'))) {
                throw 'Uninstall did not remove the application.'
            }
        }
        $report.externalSentinelPreserved = (Test-Path -LiteralPath $sentinel) -and ((Get-FileHash -LiteralPath $sentinel).Hash -eq $sentinelHash)
        if (-not $report.externalSentinelPreserved) { throw 'External sentinel changed.' }
    } finally {
        $artifactDir = Join-Path $desktopRoot '.artifacts'
        New-Item -ItemType Directory -Path $artifactDir -Force | Out-Null
        $report | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $artifactDir 'windows-smoke.json') -Encoding utf8
        $report | ConvertTo-Json | Write-Output
    }
}
if ($null -eq $report.uninstallExit) { throw 'Installed uninstaller was missing.' }
