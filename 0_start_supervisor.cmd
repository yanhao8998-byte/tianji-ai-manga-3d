@echo off
rem Launch the night self-healing supervisor for the Tianji episode pipeline.
rem Kept ASCII-only on purpose: cmd.exe parses .cmd as GBK and would mangle
rem the Chinese project path if it were written literally here.
cd /d "%~dp0"
"%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" supervisor.py
pause
