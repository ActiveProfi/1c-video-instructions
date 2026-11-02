@echo off
rem Сначала лаунчер py: на части компьютеров команды python в PATH нет.
rem Если py нет, используется python. Код возврата самой сборки сохраняется.
where py >nul 2>&1
if errorlevel 1 goto UsePython
py -3 %*
exit /b %errorlevel%
:UsePython
python %*
exit /b %errorlevel%
