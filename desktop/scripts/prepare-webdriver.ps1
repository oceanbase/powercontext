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
$runtimeRoot = "${env:ProgramFiles(x86)}/Microsoft/EdgeWebView/Application"
$runtime = Get-ChildItem -LiteralPath $runtimeRoot -Directory | Where-Object { $_.Name -match '^\d+\.\d+\.\d+\.\d+$' } | Sort-Object { [version]$_.Name } -Descending | Select-Object -First 1
if (-not $runtime) { throw 'WebView2 runtime not found.' }
$driverRoot = Join-Path $env:RUNNER_TEMP 'desktop-webdriver'
New-Item -ItemType Directory -Path $driverRoot -Force | Out-Null
$archive = Join-Path $driverRoot 'driver.zip'
Invoke-WebRequest "https://msedgedriver.microsoft.com/$($runtime.Name)/edgedriver_win64.zip" -OutFile $archive
Expand-Archive -LiteralPath $archive -DestinationPath $driverRoot
$driver = Join-Path $driverRoot 'msedgedriver.exe'
$signature = Get-AuthenticodeSignature -LiteralPath $driver
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') { throw 'WebDriver publisher verification failed.' }
"DESKTOP_EDGE_DRIVER=$driver" >> $env:GITHUB_ENV
