@echo off
rem Starts LoopMedic's guided setup as admin (Windows will ask for permission).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%~dp0Setup-LoopMedic.ps1\"'"
