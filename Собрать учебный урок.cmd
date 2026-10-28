@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Собираю учебный ролик без живой 1С. Кадры будут макетом, не вашей базой.
call "%~dp0tools\Run-Py.cmd" -m pipeline run --driver mock --tts offline
if errorlevel 1 (
  echo.
  echo Сборка не получилась. Нужен Python 3: команда py -3 или python.
  pause
  exit /b 1
)
echo.
echo Готово. Видео лежит в папке artifacts\lessons
pause
