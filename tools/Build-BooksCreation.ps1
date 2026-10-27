$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$folder = Join-Path $project 'artifacts/books-creation'
$spec = Get-Content -LiteralPath (Join-Path $project 'scenarios/books-creation-video.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($spec.voice_provider -eq 'edge-tts') {
    & 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' (Join-Path $PSScriptRoot 'narrate_books_neural.py')
    if ($LASTEXITCODE -ne 0) { throw 'Neural narration failed. Desktop voice fallback is disabled.' }
} elseif ($spec.voice_provider -eq 'elevenlabs') {
    & (Join-Path $PSScriptRoot 'Narrate-LessonElevenLabs.ps1')
} else {
    throw 'Unsupported voice provider.'
}
& 'C:\Users\admin\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' (Join-Path $PSScriptRoot 'build_books_creation.py')
if ($LASTEXITCODE -ne 0) { throw 'Video build failed.' }
