@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Read-only check of the smartphone TN VED code. Nothing is posted or deleted.
echo Проверка кода ТН ВЭД у номенклатуры «Смартфон». Проведения и удаления нет.
py -3 -m pipeline tnved --confirm-training-copy --out "%~dp0artifacts\tnved-report.json"
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
exit /b %RC%
