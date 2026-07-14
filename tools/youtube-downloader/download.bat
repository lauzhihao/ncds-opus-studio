@echo off
setlocal

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0download.ps1" %*
exit /b %ERRORLEVEL%
