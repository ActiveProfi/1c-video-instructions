param([string]$OutputPath = (Join-Path $PSScriptRoot '../artifacts/demo-result.json'))
$ErrorActionPreference = 'Stop'
$result = [ordered]@{ created_at=(Get-Date).ToUniversalTime().ToString('o'); status='failed'; video=$null; events=@(); error=$null }
$recorder = $null
. (Join-Path $PSScriptRoot 'FocusPolicy.ps1')
try {
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    Add-Type -Path (Join-Path $PSScriptRoot 'DemoRecorder.cs') -ReferencedAssemblies System.Drawing
    $ids = @(Get-Process -Name 1cv8,1cv8c -ErrorAction SilentlyContinue | ForEach-Object Id)
    $windows = [System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
    $steps = @(
        @{ name='Номер:'; caption='Шаг 1. Номер документа. Показываем поле без изменения данных.' },
        @{ name='Контрагент:'; caption='Шаг 2. Контрагент. Здесь выбирают поставщика.' },
        @{ name='Комментарий:'; caption='Шаг 3. Комментарий. Здесь можно оставить пояснение к документу.' }
    )
    $targets = @()
    foreach ($step in $steps) {
        $condition = [System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,$step.name)
        $found = @()
        foreach ($window in $windows) {
            if ($ids -notcontains $window.Current.ProcessId) { continue }
            foreach ($element in $window.FindAll([System.Windows.Automation.TreeScope]::Descendants,$condition)) {
                $c = $element.Current
                if ($c.ControlType -in @([System.Windows.Automation.ControlType]::Edit,[System.Windows.Automation.ControlType]::ComboBox) -and $c.IsEnabled -and !$c.IsOffscreen -and $c.IsKeyboardFocusable) { $found += $element }
            }
        }
        if ($found.Count -ne 1) { throw "Поле «$($step.name)» не найдено однозначно. Откройте один документ поступления и закройте всплывающие окна." }
        $pattern = $found[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
        $targets += @{ element=$found[0]; pattern=$pattern; before=$pattern.Current.Value; step=$step }
    }
    $firstRect = $targets[0].element.Current.BoundingRectangle
    $screen = [System.Windows.Forms.Screen]::FromPoint([System.Drawing.Point]::new([int]$firstRect.X,[int]$firstRect.Y)).Bounds
    $absolute = [System.IO.Path]::GetFullPath($OutputPath)
    $folder = [System.IO.Path]::GetDirectoryName($absolute)
    [System.IO.Directory]::CreateDirectory($folder) | Out-Null
    $video = Join-Path $folder ('demo-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.avi')
    $result.video = $video
    $result.focus_recoveries = 0
    $result.frames = 0
    $recorder = [DemoRecorder]::new($video,$screen.X,$screen.Y,$screen.Width,$screen.Height,6)
    $frameCount = 0
    foreach ($target in $targets) {
        $target.element.SetFocus()
        Start-Sleep -Milliseconds 250
        $check = { $target.element.Current.HasKeyboardFocus }
        $restore = {
            $state = $target.element.Current
            if (!$state.IsEnabled -or $state.IsOffscreen) { throw 'Поле заблокировано или скрыто. Закройте всплывающие окна 1С.' }
            $target.element.SetFocus()
        }
        $recoveries = Confirm-DemoFocus -Check $check -Restore $restore
        $result.focus_recoveries += $recoveries
        $stepRecoveries = $recoveries
        $rect = $target.element.Current.BoundingRectangle
        if (!$screen.Contains([System.Drawing.Rectangle]::new([int]$rect.X,[int]$rect.Y,[int]$rect.Width,[int]$rect.Height))) { throw 'Поле находится вне записываемого экрана.' }
        $result.events += @{ field=$target.step.name; start_seconds=($frameCount / 6); duration_seconds=4; bounds=@{ x=$rect.X; y=$rect.Y; width=$rect.Width; height=$rect.Height }; focus_confirmed=$true }
        for ($i=0; $i -lt 24; $i++) {
            $timer = [System.Diagnostics.Stopwatch]::StartNew()
            if (!$target.element.Current.HasKeyboardFocus) {
                $focused = [System.Windows.Automation.AutomationElement]::FocusedElement
                $result.last_focus_loss = @{ field=$target.step.name; frame=$frameCount; observed_name=$focused.Current.Name; observed_type=$focused.Current.ControlType.ProgrammaticName; observed_process=$focused.Current.ProcessId }
                if ($stepRecoveries -ge 2) { throw 'Фокус неоднократно теряется на одном поле. Запись остановлена; причина сохранена в отчёте.' }
                $recoveries = Confirm-DemoFocus -Check $check -Restore $restore -MaxAttempts (2-$stepRecoveries)
                $stepRecoveries += $recoveries
                $result.focus_recoveries += $recoveries
            }
            $rect = $target.element.Current.BoundingRectangle
            if (!$screen.Contains([System.Drawing.Rectangle]::new([int]$rect.X,[int]$rect.Y,[int]$rect.Width,[int]$rect.Height))) { throw 'Поле переместилось за границы записываемого экрана.' }
            $recorder.Frame([int]$rect.X,[int]$rect.Y,[int]$rect.Width,[int]$rect.Height,$target.step.caption)
            $frameCount++
            $result.frames=$frameCount
            $remaining = 167 - $timer.ElapsedMilliseconds
            if ($remaining -gt 0) { Start-Sleep -Milliseconds $remaining }
        }
        if ($target.before -cne $target.pattern.Current.Value) { throw 'Значение поля изменилось. Проход остановлен.' }
    }
    foreach ($target in $targets) { if ($target.before -cne $target.pattern.Current.Value) { throw 'Значение одного из полей изменилось.' } }
    $result.status='passed'
    $result.values_unchanged=$true
    $result.frames=$frameCount
    $result.duration_seconds=($frameCount / 6)
} catch { $result.error=$_.Exception.Message }
finally {
    if ($null -ne $recorder) { $recorder.Dispose() }
    $absolute = [System.IO.Path]::GetFullPath($OutputPath)
    [System.IO.Directory]::CreateDirectory([System.IO.Path]::GetDirectoryName($absolute)) | Out-Null
    $result | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $absolute -Encoding UTF8
}
if ($result.status -ne 'passed') { exit 1 }
