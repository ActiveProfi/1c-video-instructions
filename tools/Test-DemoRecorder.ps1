$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
Add-Type -Path (Join-Path $PSScriptRoot 'DemoRecorder.cs') -ReferencedAssemblies System.Drawing
$folder = Join-Path $PSScriptRoot '../artifacts'
[System.IO.Directory]::CreateDirectory($folder) | Out-Null
$path = Join-Path $folder 'recorder-synthetic-test.avi'
$recorder = [DemoRecorder]::new($path,0,0,320,240,6)
$bitmap = [System.Drawing.Bitmap]::new(320,240)
try {
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try {
        for ($i=0; $i -lt 12; $i++) {
            $graphics.Clear([System.Drawing.Color]::FromArgb($i*20,60,100))
            $recorder.WriteFrame($bitmap)
        }
    } finally { $graphics.Dispose() }
} finally { $bitmap.Dispose(); $recorder.Dispose() }
$bytes = [System.IO.File]::ReadAllBytes($path)
$ascii = [System.Text.Encoding]::ASCII.GetString($bytes)
if ($ascii.Substring(0,4) -ne 'RIFF' -or $ascii.Substring(8,4) -ne 'AVI ') { throw 'Invalid AVI signature' }
if ([BitConverter]::ToUInt32($bytes,4) -ne ($bytes.Length-8)) { throw 'Invalid RIFF length' }
$avih = $ascii.IndexOf('avih')
if ([BitConverter]::ToUInt32($bytes,$avih+24) -ne 12) { throw 'Invalid frame count' }
$idx = $ascii.LastIndexOf('idx1')
if ([BitConverter]::ToUInt32($bytes,$idx+4) -ne 192) { throw 'Invalid index size' }
$movi = $ascii.IndexOf('movi')
for ($i=0; $i -lt 12; $i++) {
    $entry = $idx+8+$i*16
    $offset = [BitConverter]::ToUInt32($bytes,$entry+8)
    $length = [BitConverter]::ToUInt32($bytes,$entry+12)
    $chunk = $movi+$offset
    if ($ascii.Substring($chunk,4) -ne '00dc') { throw 'Invalid index offset' }
    if ([BitConverter]::ToUInt32($bytes,$chunk+4) -ne $length) { throw 'Invalid indexed frame length' }
    if ($bytes[$chunk+8] -ne 255 -or $bytes[$chunk+9] -ne 216) { throw 'Invalid JPEG frame' }
}
'AVI synthetic test passed: 12 frames, valid headers, sizes and frame index.'
