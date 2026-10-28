@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo This will click through the OPEN 1C database on this PC.
echo Type YES only if it is a TRAINING copy, not the real company database.
echo Запись пойдёт в открытую базу 1С. YES — только для учебной копии.
echo Оставьте одно окно 1С на начальной странице.
echo.
set /p OK=Type YES: 
if /I not "%OK%"=="YES" (
  echo Cancelled.
  echo Отменено.
  pause
  exit /b 1
)
python -m pipeline run --driver windows --confirm-training-copy --tts auto
if errorlevel 1 (
  echo.
  echo Запись остановилась. Прочитайте текст выше: чаще всего не совпала подпись кнопки.
  pause
  exit /b 1
)
echo.
echo Готово. Видео лежит в папке artifacts\lessons
pause
