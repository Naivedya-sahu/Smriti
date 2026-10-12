@echo off
rem Smriti Console: a cmd window in the app folder so `smriti <switch>` works
rem without touching PATH. Start Menu > Smriti Console.
cd /d "%~dp0"
title Smriti Console
if /i "%~1"=="first-run" (
  echo == Smriti first-time setup ==
  smriti.exe deps
  echo.
  smriti.exe auth status
  echo.
  echo Next steps:
  echo   smriti pair ^<tablet-ip^>        one-time SSH key, types the tablet root password once
  echo   smriti link set-wifi ^<ipv4^>    tablet address on Wi-Fi / hotspot
  echo   smriti auth login chatgpt       or: smriti auth login claude / smriti auth key set ...
  echo   smriti link-test                then: smriti doctor
  echo.
  goto :eof
)
smriti.exe --help
