param(
    [int]$TargetProcessId = 0,
    [int]$MaxNodes = 1500,
    [int]$MaxDepth = 30,
    [string]$OutputPath = (Join-Path $PSScriptRoot '../artifacts/ui-probe.json')
)
$ErrorActionPreference = 'Stop'
if ($MaxNodes -lt 1 -or $MaxDepth -lt 0) { throw 'Invalid tree limits.' }
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes
$processes = @(Get-Process -Name 1cv8,1cv8c -ErrorAction SilentlyContinue)
if ($TargetProcessId) { $processes = @($processes | Where-Object Id -eq $TargetProcessId) }
$ids = @($processes | ForEach-Object Id)
$desktop = [System.Windows.Automation.AutomationElement]::RootElement
$windows = $desktop.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
$walker = [System.Windows.Automation.TreeWalker]::RawViewWalker
$nodes = [System.Collections.Generic.List[object]]::new()
$errors = [System.Collections.Generic.List[string]]::new()
$queue = [System.Collections.Generic.Queue[object]]::new()
foreach ($window in $windows) {
    try {
        if ($ids -contains $window.Current.ProcessId) { $queue.Enqueue(@{ Element=$window; Depth=0; Parent=$null }) }
    } catch { $errors.Add($_.Exception.Message) }
}
$windowCount = $queue.Count
$depthLimited = $false
while ($queue.Count -gt 0 -and $nodes.Count -lt $MaxNodes) {
    $item = $queue.Dequeue()
    try {
        $current = $item.Element.Current
        $rect = $current.BoundingRectangle
        $id = $nodes.Count
        $nodes.Add([pscustomobject]@{
            id=$id; parent=$item.Parent; depth=$item.Depth
            name=$current.Name; automation_id=$current.AutomationId
            type=$current.ControlType.ProgrammaticName
            enabled=$current.IsEnabled; offscreen=$current.IsOffscreen
            bounds=@{ x=$rect.X; y=$rect.Y; width=$rect.Width; height=$rect.Height }
            patterns=@($item.Element.GetSupportedPatterns() | ForEach-Object ProgrammaticName)
        })
        if ($item.Depth -ge $MaxDepth) { $depthLimited = $true; continue }
        $child = $walker.GetFirstChild($item.Element)
        while ($null -ne $child) {
            if (($queue.Count + $nodes.Count) -ge $MaxNodes) { break }
            $queue.Enqueue(@{ Element=$child; Depth=($item.Depth + 1); Parent=$id })
            $child = $walker.GetNextSibling($child)
        }
    } catch { $errors.Add($_.Exception.Message) }
}
$report = [ordered]@{
    created_at=(Get-Date).ToUniversalTime().ToString('o')
    status=$(if ($windowCount -eq 0) { 'no_visible_1c_window' } else { 'inspection_complete' })
    window_count=$windowCount
    node_count=$nodes.Count
    possibly_truncated=($queue.Count -gt 0 -or $nodes.Count -ge $MaxNodes -or $depthLimited)
    errors=@($errors.ToArray())
    nodes=@($nodes.ToArray())
}
$absolute = [System.IO.Path]::GetFullPath($OutputPath)
[System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($absolute)) | Out-Null
$report | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $absolute -Encoding UTF8
Write-Output "Status: $($report.status); windows: $windowCount; nodes: $($nodes.Count)"
Write-Output "Report: $absolute"
