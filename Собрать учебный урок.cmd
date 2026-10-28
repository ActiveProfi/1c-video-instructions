@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Собираю учебный ролик без живой 1С. Кадры будут макетом, не вашей базой.
python -m pipeline run --driver mock --tts offline
if errorlevel 1 (
  echo.
  echo Сборка не получилась. Если написано, что python не найден, установите Python 3 и отметьте Add python to PATH.
  pause
  exit /b 1
)
echo.
echo Готово. Видео лежит в папке artifacts\lessons
pause
