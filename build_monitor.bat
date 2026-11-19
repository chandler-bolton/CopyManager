@echo off
rem Builds the clipboard monitor (src\monitor.cpp) into bin\monitor.exe. Needs g++ (MinGW-w64 / TDM-GCC) on PATH.
rem
rem CopyManager can stay open: a running monitor.exe can't be overwritten, but it can be renamed,
rem so the old one is moved aside first. Then click "Save & refresh" in Settings or Preferences
rem to switch to the new build (or restart CopyManager).
cd /d "%~dp0"
if not exist bin mkdir bin
if exist bin\monitor.old.exe del /q bin\monitor.old.exe 2>nul
if exist bin\monitor.exe move /y bin\monitor.exe bin\monitor.old.exe >nul
g++ -std=c++17 -O2 -Wall -municode -mwindows -static -o bin\monitor.exe src\monitor.cpp -lshell32
if errorlevel 1 (
    echo Build failed.
    if exist bin\monitor.old.exe if not exist bin\monitor.exe move /y bin\monitor.old.exe bin\monitor.exe >nul
    exit /b 1
)
echo Built bin\monitor.exe
