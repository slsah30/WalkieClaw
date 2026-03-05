@echo off
title WalkieClaw UDP Relay
cd /d C:\users\tjc30\aipi-openclaw
:loop
echo Starting UDP relay...
python upd_relay.py
echo Relay crashed, restarting in 3 seconds...
timeout /t 3 /nobreak >nul
goto loop
