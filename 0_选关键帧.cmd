@echo off
chcp 65001 >nul
setlocal
set "ROOT=%~dp0"
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

echo ==================================================
echo   《天纪》AI 漫剧 3D 化 - 阶段 2/3
echo   镜头分段 + 关键帧选取（运动量自适应）
echo ==================================================
echo.

"%PY%" "%ROOT%shots_and_keys.py"

echo.
echo --------------------------------------------------
echo 分镜表：02_镜头分段\分镜表.txt
echo 关键帧：03_关键帧_原图\  总览图：_预览\关键帧总览.jpg
echo --------------------------------------------------
pause
endlocal
