param([Parameter(Mandatory=$true)][string]$VoiceId)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$catalog = Get-Content -LiteralPath (Join-Path $project 'config/voices.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$voice = @($catalog.voices | Where-Object { $_.id -eq $VoiceId -and $_.preferred })
if ($voice.Count -ne 1) { throw 'Choose one approved voice from config/voices.json.' }
$path = Join-Path $project 'scenarios/books-creation-video.json'
$spec = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
$spec.voice = $VoiceId
$spec | Add-Member -NotePropertyName voice_gender -NotePropertyValue $voice[0].gender -Force
$spec.voice_provider = $catalog.provider
if ($voice[0].provider) { $spec.voice_provider = $voice[0].provider }
$spec.audio_directory = "neural-voice/$VoiceId"
$spec.output_filename = "books-upd-instruction-$VoiceId.mp4"
[IO.File]::WriteAllText($path, ($spec | ConvertTo-Json -Depth 20), (New-Object Text.UTF8Encoding($true)))
Write-Output $voice[0].name
