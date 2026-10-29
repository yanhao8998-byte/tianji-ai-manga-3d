@echo off
rem ============================================================
rem  Tianji pipeline: stop Windows Update from force-rebooting
rem  the machine mid-render.  RIGHT-CLICK -> Run as administrator.
rem
rem  Background: at 2026-10-15 04:19 Windows Update (MoUsoCoreWorker)
rem  force-rebooted the PC and the render pipeline sat idle for 4.3h.
rem  This sets two documented policies:
rem    1) NoAutoRebootWithLoggedOnUsers = do not auto-reboot while a
rem       user is logged on (update installs, but waits for a manual
rem       restart)
rem    2) Active hours 08:00-23:59 so automatic maintenance restarts
rem       are pushed out of the rendering window
rem  Fully reversible: delete the policy value or run "gpupdate /force".
rem ============================================================

net session >nul 2>&1
if errorlevel 1 (
  echo.
  echo  [X] Administrator rights are required.
  echo      Right-click this file and choose "Run as administrator".
  echo.
  pause
  exit /b 1
)

reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU" /v NoAutoRebootWithLoggedOnUsers /t REG_DWORD /d 1 /f
reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v ActiveHoursStart /t REG_DWORD /d 8 /f
reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v ActiveHoursEnd /t REG_DWORD /d 23 /f
reg add "HKLM\SOFTWARE\Microsoft\WindowsUpdate\UX\Settings" /v SmartActiveHoursState /t REG_DWORD /d 1 /f

echo.
echo  [OK] Windows Update will no longer force a reboot while you are logged on.
echo       Updates still install; they just wait for a manual restart.
echo.
pause
