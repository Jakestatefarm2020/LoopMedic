@echo off
rem Removes LoopMedic completely (Windows will ask for permission).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -NoExit -ExecutionPolicy Bypass -File \"%~dp0Install-LoopMedic.ps1\" -Uninstall'"
