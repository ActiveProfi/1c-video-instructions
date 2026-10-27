function Read-ProjectEnv {
    param([Parameter(Mandatory=$true)][string]$Path)
    $settings = @{}
    if (!(Test-Path -LiteralPath $Path)) { return $settings }
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*([A-Z][A-Z0-9_]*)\s*=(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim()
            if ($value.Length -ge 2 -and (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'")))) {
                $value = $value.Substring(1,$value.Length-2)
            }
            $settings[$name] = $value
        }
    }
    return $settings
}
