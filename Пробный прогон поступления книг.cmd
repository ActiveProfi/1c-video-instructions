@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Пробный прогон поступления книг. Кнопки не нажимаются, видео не пишется.
echo База не восстанавливается. Старые документы не открываются и не удаляются.
py -3 -m pipeline live --lesson "%~dp0lessons\books-upd.yaml" --dry-run --confirm-training-copy
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
exit /b %RC%
