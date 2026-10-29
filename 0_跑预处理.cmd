@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

echo ==================================================
echo   《天纪》AI 漫剧 3D 化 - 第一集预处理
echo   诊断 - 去隔行 - 降噪 - 色彩校正 - 降帧 12fps
echo ==================================================
echo.

"%PY%" "%ROOT%preprocess.py" %*

echo.
echo --------------------------------------------------
echo 完成。引导视频在 01_引导视频\guide.mp4
echo 完整日志在 logs\预处理日志.txt
echo --------------------------------------------------
pause
endlocal
