$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'Read-ProjectEnv.ps1')
. (Join-Path $PSScriptRoot 'Get-PronouncedText.ps1')
$settings = Read-ProjectEnv -Path (Join-Path $project '.env')
$apiKey = $env:ELEVENLABS_API_KEY
if (!$apiKey) { $apiKey = $settings['ELEVENLABS_API_KEY'] }
if (!$apiKey) { throw 'ElevenLabs key is missing in .env.' }
$spec = Get-Content -LiteralPath (Join-Path $project 'scenarios/books-creation-video.json') -Raw -Encoding UTF8 | ConvertFrom-Json
if ($spec.voice_provider -ne 'elevenlabs') { throw 'The selected voice is not an ElevenLabs voice.' }
$model = $settings['ELEVENLABS_MODEL_ID']
if (!$model) { $model = 'eleven_multilingual_v2' }
$folder = Join-Path (Join-Path $project 'artifacts/books-creation') $spec.audio_directory
[void][IO.Directory]::CreateDirectory($folder)
$ffmpeg = Join-Path $PSScriptRoot 'media-runtime/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe'
$headers = @{ 'xi-api-key'=$apiKey }
for ($i=0; $i -lt $spec.scenes.Count; $i++) {
    $scene = $spec.scenes[$i]
    $speech = Get-PronouncedText -Text $scene.speech
    $hash = [Security.Cryptography.SHA256]::Create()
    try { $signature = [BitConverter]::ToString($hash.ComputeHash([Text.Encoding]::UTF8.GetBytes($spec.voice + "`n" + $model + "`n" + $speech))).Replace('-','').ToLowerInvariant() }
    finally { $hash.Dispose() }
    $cachePath = Join-Path $folder "voice-$i.json"
    $wavPath = Join-Path $folder "voice-$i.wav"
    if ((Test-Path -LiteralPath $cachePath) -and (Test-Path -LiteralPath $wavPath)) {
        $cache = Get-Content -LiteralPath $cachePath -Raw | ConvertFrom-Json
        if ($cache.signature -eq $signature -and (Get-Item -LiteralPath $wavPath).Length -gt 1000) { Write-Output "Voice ${i}: cached"; continue }
    }
    $mp3Path = Join-Path $folder "voice-$i.mp3"
    $body = @{ text=$speech; model_id=$model; voice_settings=@{ stability=0.45; similarity_boost=0.75; style=0; use_speaker_boost=$true; speed=1 } } | ConvertTo-Json -Depth 5
    try { Invoke-WebRequest -Uri "https://api.elevenlabs.io/v1/text-to-speech/$($spec.voice)?output_format=mp3_44100_128" -Headers $headers -Method Post -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile $mp3Path -UseBasicParsing }
    catch { throw "Voice ${i}: ElevenLabs synthesis failed. Check permissions and credits." }
    & $ffmpeg -hide_banner -loglevel error -y -i $mp3Path -ar 24000 -ac 1 $wavPath
    if ($LASTEXITCODE -ne 0) { throw "Voice ${i}: audio conversion failed." }
    [IO.File]::WriteAllText($cachePath,(@{ signature=$signature; voice=$spec.voice; provider='elevenlabs'; model=$model } | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    Write-Output "Voice ${i}: generated"
}
