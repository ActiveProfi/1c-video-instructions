@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo This records lesson 1 in the OPEN 1C window.
echo Type YES only if that window is the training copy, organization Alfa OOO.
echo Запись урока 1 в открытой 1С. YES — только если это учебная база «Альфа ООО».
echo Остановка в любой момент: клавиша Esc. Удалять данные программа не будет.
echo.
set /p OK=Type YES: 
if /I not "%OK%"=="YES" (
  echo Cancelled.
  exit /b 1
)
py -3 -m pipeline live --lesson "%~dp0lessons\lesson1.yaml" --confirm-training-copy --tts auto --out "%~dp0artifacts\lessons\lesson1"
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
exit /b %RC%
