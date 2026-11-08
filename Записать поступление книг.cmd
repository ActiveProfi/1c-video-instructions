@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo Свежая запись поступления книг в ОТКРЫТОЙ учебной базе.
echo YES — только если это учебная копия, организация Альфа ООО.
echo Программа создаст НОВЫЙ документ. УЧ-КНИГИ-001 и УЧ-КНИГИ-002 не трогает.
echo Базу не восстанавливает и ничего не удаляет. Остановка: клавиша Esc.
echo Голос: Лили, модель eleven_multilingual_v2. Ключ берётся из .env, не из этой команды.
echo.
set /p OK=Type YES: 
if /I not "%OK%"=="YES" (
  echo Cancelled.
  exit /b 1
)
py -3 -m pipeline live --lesson "%~dp0lessons\books-upd.yaml" --confirm-training-copy --tts elevenlabs --voice pFZP5JQG7iQjIQuC4Bku
set RC=%ERRORLEVEL%
if not "%RC%"=="0" pause
exit /b %RC%
