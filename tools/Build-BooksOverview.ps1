$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$lessonPath = Join-Path $project 'artifacts/books-lesson'
$sceneSpec = Get-Content -LiteralPath (Join-Path $project 'scenarios/books-overview.json') -Raw -Encoding UTF8 | ConvertFrom-Json
Add-Type -AssemblyName System.Speech
$speaker = New-Object System.Speech.Synthesis.SpeechSynthesizer
try {
    $voice = $speaker.GetInstalledVoices() | Where-Object { $_.Enabled -and $_.VoiceInfo.Culture.Name -eq 'ru-RU' } | Select-Object -First 1
    if (!$voice) { throw 'Русский голос Windows не найден.' }
    $speaker.SelectVoice($voice.VoiceInfo.Name)
    $speaker.Rate = 1
    for ($i = 0; $i -lt $sceneSpec.scenes.Count; $i++) {
        $speaker.SetOutputToWaveFile((Join-Path $lessonPath ('voice-{0}.wav' -f $i)))
        $speaker.Speak($sceneSpec.scenes[$i].speech)
        $speaker.SetOutputToNull()
    }
} finally { $speaker.Dispose() }
$python = 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
& $python (Join-Path $PSScriptRoot 'build_books_overview.py')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать MP4.' }
