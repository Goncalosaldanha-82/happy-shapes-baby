@echo off
rem Baby Sensory daily run (Task Scheduler task "BabySensory": 08:30, at log-on, wakes the PC, catches up).
rem 1) uploads + SCHEDULES ready episodes on YouTube at 06:00 on the next free days (YouTube publishes them,
rem    so 06:00 works even with this PC off), keeping up to 7 days scheduled ahead;
rem 2) renders new episodes so 2 are always ready for the next run.
rem Uploading first means a slow render never delays the schedule. Re-running the same day is harmless.
cd /d "%~dp0"
set PY=C:\Users\barba\AppData\Local\Programs\Python\Python313\python.exe
set PYTHONIOENCODING=utf-8
echo ==== %date% %time% ==== >> daily.log
"%PY%" upload.py --unattended >> daily.log 2>&1
if errorlevel 1 (echo UPLOAD FAILED >> daily.log)
set RC=0
"%PY%" make_video.py --ahead 2 >> daily.log 2>&1
if errorlevel 1 (echo RENDER FAILED >> daily.log & set RC=1)
rem newly rendered episodes go up straight away if the quota allows (also after a failed render)
"%PY%" upload.py --unattended >> daily.log 2>&1
if errorlevel 1 (echo UPLOAD FAILED >> daily.log & exit /b 2)
if %RC%==0 echo OK >> daily.log
exit /b %RC%
