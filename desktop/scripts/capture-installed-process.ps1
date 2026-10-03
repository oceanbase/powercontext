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

param([Parameter(Mandatory)][int]$ApplicationPid, [Parameter(Mandatory)][string]$ArtifactDirectory)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true') { throw 'Disposable CI runner required.' }
$allProcesses = @(Get-CimInstance Win32_Process)
$owned = [System.Collections.Generic.HashSet[int]]::new()
[void]$owned.Add($ApplicationPid)
do {
    $added = $false
    foreach ($candidate in $allProcesses) {
        if ($owned.Contains([int]$candidate.ParentProcessId) -and $owned.Add([int]$candidate.ProcessId)) { $added = $true }
    }
} while ($added)
$details = @($allProcesses | Where-Object { $owned.Contains([int]$_.ProcessId) } | ForEach-Object {
    $process = Get-Process -Id $_.ProcessId -ErrorAction SilentlyContinue
    [ordered]@{
        pid = $_.ProcessId; parentPid = $_.ParentProcessId; name = $_.Name
        commandLine = $_.CommandLine; sessionId = $_.SessionId
        responding = $process.Responding; windowTitle = $process.MainWindowTitle
        windowHandle = $process.MainWindowHandle.ToInt64()
    }
})
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
[ordered]@{ runnerSessionId = (Get-Process -Id $PID).SessionId; runnerElevated = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator); processes = $details } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $ArtifactDirectory 'installed-ui-processes.json') -Encoding utf8
$app = Get-Process -Id $ApplicationPid -ErrorAction SilentlyContinue
if (-not $app -or $app.MainWindowHandle -eq [IntPtr]::Zero) { exit 0 }
Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class InstalledWindowCapture {
    [StructLayout(LayoutKind.Sequential)] public struct Rect { public int Left, Top, Right, Bottom; }
    [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr window, out Rect rect);
    [DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr window, IntPtr target, uint flags);
}
"@
$bounds = [InstalledWindowCapture+Rect]::new()
if (-not [InstalledWindowCapture]::GetWindowRect($app.MainWindowHandle, [ref]$bounds)) { exit 0 }
$width = $bounds.Right - $bounds.Left
$height = $bounds.Bottom - $bounds.Top
if ($width -le 0 -or $height -le 0 -or $width -gt 4096 -or $height -gt 4096) { exit 0 }
$bitmap = [System.Drawing.Bitmap]::new($width, $height)
try {
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        $context = $graphics.GetHdc()
        try { $captured = [InstalledWindowCapture]::PrintWindow($app.MainWindowHandle, $context, 2) }
        finally { $graphics.ReleaseHdc($context) }
    } finally { $graphics.Dispose() }
    if ($captured) { $bitmap.Save((Join-Path $ArtifactDirectory 'installed-ui-window.png')) }
} finally { $bitmap.Dispose() }
