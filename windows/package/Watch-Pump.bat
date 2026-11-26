@echo off
rem Live view of fan speeds + CPU temperature for 90 s (Windows will ask for permission).
powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -NoExit -ExecutionPolicy Bypass -File \"%~dp0Watch-Pump.ps1\"'"
