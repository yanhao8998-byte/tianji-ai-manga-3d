@echo off
chcp 65001 >nul
title 阶段4 回填对齐
cd /d "%~dp0"
"%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe" stage4_align.py
echo.
echo ==============================
echo  对齐完成，窗口可关闭
pause
