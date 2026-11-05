@echo off
rem Launcher for cmd.exe users: works from any directory.
rem
rem   D:\code\oceanbase\powercontext\minimax\run.cmd state history
rem   D:\code\oceanbase\powercontext\minimax\run.cmd agent --step apply --patch agent\code\author-fix.patch --worktree D:\code\seekdb
rem
rem Why this exists: in cmd.exe `cd D:\...` does not switch drives, so running
rem `python run.py` from C: fails with "can't open file 'C:\Users\...\run.py'".
rem
rem Set PYTHON to a specific interpreter if `python` on PATH is not the one with
rem pymysql installed, e.g.  set PYTHON=D:\develop\python\python.exe

setlocal
cd /d "%~dp0"

if "%PYTHON%"=="" set "PYTHON=python"
"%PYTHON%" "%~dp0run.py" %*
exit /b %ERRORLEVEL%
