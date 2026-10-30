@echo off
chcp 65001 >nul
rem 双击本文件即可打开 HCC-Fit（使用包内已构建的 exe，不需要 Python）
set "HERE=%~dp0"
set "EXE=%HERE%desktop_build\HCC-Fit\HCC-Fit.exe"
if not exist "%EXE%" (
  echo 找不到 %EXE%
  echo 请确认整个 HCC-Fit 文件夹完整；或在 desktop 目录运行 run_desktop.bat。
  pause
  exit /b 1
)
start "" "%EXE%"
