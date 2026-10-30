@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -m PyInstaller --noconfirm HCC-Fit.spec
if errorlevel 1 pause
