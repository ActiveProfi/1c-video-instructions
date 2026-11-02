@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Dry run of lesson 1: search only, no posting and no recording.
echo Пробный прогон урока 1. Кнопки «Провести» и «Отправить» не нажимаются. Видео не пишется.
py -3 -m pipeline live --lesson "%~dp0lessons\lesson1.yaml" --dry-run --confirm-training-copy --out "%~dp0artifacts\lessons\lesson1-dry"
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
exit /b %RC%
