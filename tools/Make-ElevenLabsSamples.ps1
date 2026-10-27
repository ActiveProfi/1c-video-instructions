param([switch]$GenerateSamples)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'Read-ProjectEnv.ps1')
$projectEnv = Read-ProjectEnv -Path (Join-Path $project '.env')
$publicVoices = $env:ELEVENLABS_PUBLIC_VOICES -eq '1'
$modelId = $projectEnv['ELEVENLABS_MODEL_ID']
if (!$modelId) { $modelId = 'eleven_multilingual_v2' }
$keyPath = Join-Path $project 'config/elevenlabs-key.dpapi'
$apiKey = $env:ELEVENLABS_API_KEY
if (!$apiKey) { $apiKey = $projectEnv['ELEVENLABS_API_KEY'] }
if (!$apiKey -and (Test-Path -LiteralPath $keyPath)) {
    $secure = ConvertTo-SecureString ([IO.File]::ReadAllText($keyPath))
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
    try { $apiKey = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer) }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
}
if (!$apiKey) { throw 'ElevenLabs key not configured. Use the local connection window.' }
$headers = @{ 'xi-api-key'=$apiKey }
$folder = Join-Path $project 'artifacts/voice-tests-elevenlabs'
[void][IO.Directory]::CreateDirectory($folder)
try {
    if ($publicVoices) {
        # Published premade catalog; no account credentials or private voices.
        $response = Invoke-RestMethod -Uri 'https://api.elevenlabs.io/v1/voices' -Method Get
    } else {
        $response = Invoke-RestMethod -Uri 'https://api.elevenlabs.io/v2/voices?page_size=100&gender=female' -Headers $headers -Method Get
    }
} catch { throw 'ElevenLabs connection failed. Check the key and Voices Read permission.' }
$candidates = @($response.voices | Where-Object { $_.labels.gender -eq 'female' })
if ($candidates.Count -lt 4) { throw 'Fewer than four female voices are available in this account.' }
$ranked = @($candidates | Sort-Object @{ Expression = {
    $score = 0
    if ($_.labels.language -match 'ru|russian') { $score += 10 }
    if (($_.description + ' ' + ($_.labels | ConvertTo-Json -Compress)) -match 'warm|soft|calm|conversational|gentle') { $score += 3 }
    $score
}; Descending = $true }, name | Select-Object -First 4)
$text = 'В этом видео разберём, как создать документ поступления товаров в программе один эс: Бухгалтерия предприятия, редакция три ноль. На примере книг оформим УПД и посмотрим проводки. Начнём с раздела Покупки.'
$plan = @()
for ($i=0; $i -lt $ranked.Count; $i++) {
    $voice = $ranked[$i]
    $plan += [pscustomobject]@{ number=$i+1; name=$voice.name; voice_id=$voice.voice_id; file="sample-$($i+1)-$($voice.voice_id).mp3" }
}
[IO.File]::WriteAllText((Join-Path $folder 'samples.json'), ([pscustomobject]@{ provider='elevenlabs'; model=$modelId; speed=1; text=$text; voices=$plan } | ConvertTo-Json -Depth 8), (New-Object Text.UTF8Encoding($true)))
$plan | Select-Object number,name,voice_id
if (!$GenerateSamples) { return }
foreach ($voice in $plan) {
    $output = Join-Path $folder $voice.file
    if ((Test-Path -LiteralPath $output) -and (Get-Item -LiteralPath $output).Length -gt 1000) { Write-Output "Sample $($voice.number): cached"; continue }
    $body = @{ text=$text; model_id=$modelId; voice_settings=@{ stability=0.45; similarity_boost=0.75; style=0; use_speaker_boost=$true; speed=1 } } | ConvertTo-Json -Depth 5
    try {
        Invoke-WebRequest -Uri "https://api.elevenlabs.io/v1/text-to-speech/$($voice.voice_id)?output_format=mp3_44100_128" -Headers $headers -Method Post -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($body)) -OutFile $output -UseBasicParsing
    } catch { throw "Sample $($voice.number) failed. Check Text to Speech permission, available credits and voice access." }
    Write-Output "Sample $($voice.number): created"
}
