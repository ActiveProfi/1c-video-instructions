function Get-PronouncedText {
    param([Parameter(Mandatory=$true)][string]$Text)
    $rulesPath = Join-Path $PSScriptRoot '../config/pronunciation.json'
    $dictionary = Get-Content -LiteralPath $rulesPath -Raw -Encoding UTF8 | ConvertFrom-Json
    foreach ($rule in $dictionary.rules) {
        $replacement = [string]$rule.pronunciation
        $pattern = '\b' + [regex]::Escape($rule.word) + '\b'
        $Text = [regex]::Replace($Text,$pattern,[Text.RegularExpressions.MatchEvaluator]{
            param($matched)
            $word = $matched.Value
            if ($word -ceq $word.ToUpperInvariant()) { return $replacement.ToUpperInvariant() }
            if ([char]::IsUpper($word[0])) { return $replacement.Substring(0,1).ToUpperInvariant() + $replacement.Substring(1) }
            return $replacement
        },[Text.RegularExpressions.RegexOptions]::IgnoreCase)
    }
    return $Text
}
