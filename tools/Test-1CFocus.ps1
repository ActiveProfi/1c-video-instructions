param([string]$OutputPath = (Join-Path $PSScriptRoot '../artifacts/focus-test.json'))
$ErrorActionPreference = 'Stop'
$result = [ordered]@{ created_at=(Get-Date).ToUniversalTime().ToString('o'); status='failed'; target='Комментарий:'; value_unchanged=$null; error=$null }
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    $ids = @(Get-Process -Name 1cv8,1cv8c -ErrorAction SilentlyContinue | ForEach-Object Id)
    $desktop = [System.Windows.Automation.AutomationElement]::RootElement
    $windows = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
    $condition = [System.Windows.Automation.AndCondition]::new(
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty, 'Комментарий:'),
        [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::ControlTypeProperty, [System.Windows.Automation.ControlType]::Edit)
    )
    $matches = [System.Collections.Generic.List[object]]::new()
    foreach ($window in $windows) {
        if ($ids -notcontains $window.Current.ProcessId) { continue }
        $elements = $window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition)
        foreach ($element in $elements) {
            $state = $element.Current
            if ($state.IsEnabled -and !$state.IsOffscreen -and $state.IsKeyboardFocusable) { $matches.Add($element) }
        }
    }
    if ($matches.Count -ne 1) { throw "Expected one visible Comment field; found $($matches.Count). Open one document in the training database." }
    $target = $matches[0]
    $pattern = $target.GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    $before = $pattern.Current.Value
    $target.SetFocus()
    Start-Sleep -Milliseconds 500
    $result.value_unchanged = ($before -ceq $pattern.Current.Value)
    $result.focus_confirmed = $target.Current.HasKeyboardFocus
    if (!$result.value_unchanged) { throw 'The field value changed during the test. No text was sent by this script.' }
    if (!$result.focus_confirmed) { throw 'Keyboard focus was not confirmed by Windows UI Automation.' }
    $result.status = 'passed'
} catch { $result.error = $_.Exception.Message }
$absolute = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($absolute)) | Out-Null
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $absolute -Encoding UTF8
Write-Host "Result: $($result.status)"
if ($result.error) { Write-Host $result.error }
Write-Host "Report: $absolute"
if ($result.status -ne 'passed') { exit 1 }
