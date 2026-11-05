@echo off
rem One command that reproduces the whole verification chain.
rem
rem   D:\code\oceanbase\powercontext\minimax\verify.cmd
rem   D:\code\oceanbase\powercontext\minimax\verify.cmd --worktree D:\code\seekdb --keep
rem
rem Requires: seekdb running (python scripts\init_db.py) and a clean seekdb checkout.
rem Set PYTHON to a specific interpreter when `python` on PATH lacks pymysql.

setlocal
cd /d "%~dp0"

if "%PYTHON%"=="" set "PYTHON=python"
"%PYTHON%" "%~dp0scripts\verify_all.py" %*
exit /b %ERRORLEVEL%
