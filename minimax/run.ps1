# Launcher for PowerShell users: works from any directory.
#
#   D:\code\oceanbase\powercontext\minimax\run.ps1 state history
#   D:\code\oceanbase\powercontext\minimax\run.ps1 agent --step apply --patch agent\code\author-fix.patch --worktree D:\code\seekdb
#
# If script execution is blocked, call it explicitly:
#   powershell -ExecutionPolicy Bypass -File run.ps1 state history

param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Args)

$here = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $here
$python = $env:PYTHON
if (-not $python) { $python = "python" }
& $python (Join-Path $here "run.py") @Args
exit $LASTEXITCODE
