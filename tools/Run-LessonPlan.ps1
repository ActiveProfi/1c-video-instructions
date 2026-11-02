param(
    [Parameter(Mandatory = $true)][string]$PlanPath,
    [Parameter(Mandatory = $true)][string]$OutputDir,
    [switch]$ConfirmTrainingCopy,
    [switch]$DryRun,
    [int]$TargetProcessId = 0
)
# Локальный проход по учебной базе 1С в сеансе Windows.
# Скрипт нажимает только то, что перечислено в plan.json, снимает окно и пишет journal.json.
$ErrorActionPreference = 'Stop'
$journal = [ordered]@{
    driver = 'windows'
    status = 'failed'
    mocked = $false
    dry_run = [bool]$DryRun
    training_copy = [bool]$ConfirmTrainingCopy
    raw_screen = $null
    events = @()
    error = $null
}
$grab = $null

function Write-Journal {
    $folder = [IO.Path]::GetFullPath($OutputDir)
    [void][IO.Directory]::CreateDirectory($folder)
    $path = Join-Path $folder 'journal.json'
    $json = $journal | ConvertTo-Json -Depth 8
    [IO.File]::WriteAllText($path, $json, (New-Object Text.UTF8Encoding $false))
}

function Read-Utf8Json([string]$Path) {
    $bytes = [IO.File]::ReadAllBytes($Path)
    $text = [Text.Encoding]::UTF8.GetString($bytes)
    if ($text.Length -gt 0 -and [int]$text[0] -eq 0xFEFF) { $text = $text.Substring(1) }
    return ($text | ConvertFrom-Json)
}

function Quote-Arg([string]$Value) {
    if ($Value -match '[\s"]') { return '"' + ($Value -replace '"', '\"') + '"' }
    return $Value
}

try {
    if (-not $ConfirmTrainingCopy) {
        throw 'Добавьте -ConfirmTrainingCopy только если открыта учебная копия базы, не рабочая.'
    }
    Add-Type -AssemblyName UIAutomationClient
    Add-Type -AssemblyName UIAutomationTypes
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -AssemblyName System.Drawing
    if (-not ('NativeMouse' -as [type])) {
        Add-Type -TypeDefinition @'
using System.Runtime.InteropServices;
public static class NativeMouse {
    [DllImport("user32.dll")]
    public static extern void mouse_event(int flags, int dx, int dy, int data, int extra);
}
'@
    }
    $plan = Read-Utf8Json $PlanPath
    $deny = @('отправить', 'фнс', 'клиент-банк', 'клиент банк', 'выгрузить платеж', 'подписать и отправить')
    $frames = Join-Path ([IO.Path]::GetFullPath($OutputDir)) 'frames'
    [void][IO.Directory]::CreateDirectory($frames)

    $processes = @(Get-Process -Name 1cv8, 1cv8c -ErrorAction SilentlyContinue)
    if ($TargetProcessId) { $processes = @($processes | Where-Object Id -eq $TargetProcessId) }
    $ids = @($processes | ForEach-Object Id)
    $desktop = [System.Windows.Automation.AutomationElement]::RootElement
    $children = $desktop.FindAll(
        [System.Windows.Automation.TreeScope]::Children,
        [System.Windows.Automation.Condition]::TrueCondition
    )
    $windows = @()
    foreach ($child in $children) {
        try {
            if ($ids -contains $child.Current.ProcessId) { $windows += $child }
        } catch {}
    }
    if ($windows.Count -eq 0) {
        throw 'Окно 1С не найдено. Откройте учебную базу в этом сеансе Windows и оставьте её на экране.'
    }
    if ($windows.Count -gt 1) {
        throw 'Открыто несколько окон 1С. Закройте лишние или запустите команду с --process-id.'
    }
    $window = $windows[0]

    $ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
    if ($ffmpeg -and -not $DryRun) {
        $bounds = $window.Current.BoundingRectangle
        $width = [int]$bounds.Width - ([int]$bounds.Width % 2)
        $height = [int]$bounds.Height - ([int]$bounds.Height % 2)
        if ($width -ge 2 -and $height -ge 2) {
            $rawPath = Join-Path ([IO.Path]::GetFullPath($OutputDir)) 'raw-screen.mp4'
            $grab = New-Object System.Diagnostics.Process
            $grab.StartInfo.FileName = $ffmpeg.Source
            $grab.StartInfo.Arguments = @(
                '-hide_banner', '-loglevel', 'error', '-y',
                '-f', 'gdigrab', '-framerate', '8',
                '-offset_x', [string][int]$bounds.X,
                '-offset_y', [string][int]$bounds.Y,
                '-video_size', ($width.ToString() + 'x' + $height.ToString()),
                '-i', 'desktop',
                '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                (Quote-Arg $rawPath)
            ) -join ' '
            $grab.StartInfo.UseShellExecute = $false
            $grab.StartInfo.RedirectStandardInput = $true
            $grab.StartInfo.CreateNoWindow = $true
            if ($grab.Start()) { $journal.raw_screen = 'raw-screen.mp4' } else { $grab = $null }
        }
    }

    function Find-StepElement($Name, $ControlType) {
        $condition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::NameProperty,
            $Name
        )
        $found = @($window.FindAll([System.Windows.Automation.TreeScope]::Descendants, $condition))
        $visible = @($found | Where-Object {
            try { -not $_.Current.IsOffscreen -and $_.Current.IsEnabled } catch { $false }
        })
        if ($visible.Count -gt 1 -and $ControlType) {
            $fragment = switch ($ControlType) {
                'button' { 'Button' }
                'edit' { 'Edit' }
                'link' { 'Hyperlink' }
                'check' { 'CheckBox' }
                'text' { 'Text' }
                default { '' }
            }
            if ($fragment) {
                $typed = @($visible | Where-Object { $_.Current.ControlType.ProgrammaticName -like ('*' + $fragment + '*') })
                if ($typed.Count -ge 1) { $visible = $typed }
            }
        }
        if ($visible.Count -lt 1) {
            throw "Не найден элемент «$Name». Откройте нужную форму. Точную подпись можно скопировать из отчёта tools\Inspect-1C.ps1 в поле automation_name файла plan.json."
        }
        if ($visible.Count -gt 1) {
            throw "Элемент «$Name» найден несколько раз. Оставьте на экране одну такую подпись."
        }
        return $visible[0]
    }

    function Convert-Bounds($Element) {
        $win = $window.Current.BoundingRectangle
        $rect = $Element.Current.BoundingRectangle
        return [ordered]@{
            x = [int]($rect.X - $win.X)
            y = [int]($rect.Y - $win.Y)
            width = [int]$rect.Width
            height = [int]$rect.Height
        }
    }

    function Save-Window([string]$Path) {
        $rect = $window.Current.BoundingRectangle
        $width = [Math]::Max(1, [int]$rect.Width)
        $height = [Math]::Max(1, [int]$rect.Height)
        $bitmap = New-Object System.Drawing.Bitmap $width, $height
        try {
            $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
            try {
                $graphics.CopyFromScreen([int]$rect.X, [int]$rect.Y, 0, 0, $bitmap.Size)
            } finally { $graphics.Dispose() }
            $bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
        } finally { $bitmap.Dispose() }
    }

    function Invoke-Click($Element) {
        $invoke = [System.Windows.Automation.InvokePattern]::Pattern
        foreach ($pattern in @($Element.GetSupportedPatterns())) {
            if ($pattern.ProgrammaticName -eq $invoke.ProgrammaticName) {
                $Element.GetCurrentPattern($invoke).Invoke()
                return
            }
        }
        $rect = $Element.Current.BoundingRectangle
        $x = [int]($rect.X + ($rect.Width / 2))
        $y = [int]($rect.Y + ($rect.Height / 2))
        [System.Windows.Forms.Cursor]::Position = New-Object System.Drawing.Point $x, $y
        Start-Sleep -Milliseconds 120
        [NativeMouse]::mouse_event(0x0002, 0, 0, 0, 0)
        [NativeMouse]::mouse_event(0x0004, 0, 0, 0, 0)
    }

    function Set-Field($Element, [string]$Value) {
        $valuePattern = [System.Windows.Automation.ValuePattern]::Pattern
        foreach ($pattern in @($Element.GetSupportedPatterns())) {
            if ($pattern.ProgrammaticName -eq $valuePattern.ProgrammaticName) {
                $Element.GetCurrentPattern($valuePattern).SetValue($Value)
                return
            }
        }
        throw "Поле нельзя заполнить через интерфейс Windows. Проверьте, что это поле ввода, и при необходимости впишите значение вручную в учебный документ."
    }

    function Read-Field($Element) {
        $valuePattern = [System.Windows.Automation.ValuePattern]::Pattern
        foreach ($pattern in @($Element.GetSupportedPatterns())) {
            if ($pattern.ProgrammaticName -eq $valuePattern.ProgrammaticName) {
                return [string]$Element.GetCurrentPattern($valuePattern).Current.Value
            }
        }
        return [string]$Element.Current.Name
    }

    $events = [System.Collections.Generic.List[object]]::new()
    foreach ($step in @($plan.steps)) {
        $blob = ((@($step.automation_name, $step.caption, $step.value, $step.expected) | Where-Object { $_ }) -join ' ').ToLower().Replace('ё', 'е')
        foreach ($word in $deny) {
            if ($blob.Contains($word)) {
                throw "Шаг «$($step.automation_name)» похож на отправку отчёта или платёж. Учебный ролик его не выполняет."
            }
        }
        $element = Find-StepElement $step.automation_name $step.control_type
        $before = $null
        try { $before = Convert-Bounds $element } catch { $before = $null }
        if (-not $DryRun) {
            switch ($step.action) {
                'click' { Invoke-Click $element }
                'set_text' { Set-Field $element ([string]$step.value) }
                'check' {
                    if ($step.expected) {
                        $actual = Read-Field $element
                        if ($actual -notlike ('*' + [string]$step.expected + '*')) {
                            throw "Проверка «$($step.caption)» не сошлась. Ожидали «$($step.expected)», на экране «$actual». Исправьте учебные данные или план и соберите урок снова."
                        }
                    }
                }
                default { $element.SetFocus() }
            }
            Start-Sleep -Milliseconds 450
        }
        $bounds = $before
        try { $bounds = Convert-Bounds $element } catch {}
        $relative = 'frames/' + $step.id + '.png'
        Save-Window (Join-Path $frames ($step.id + '.png'))
        $events.Add([ordered]@{
            step_id = [string]$step.id
            scene_id = [string]$step.scene_id
            action = [string]$step.action
            target = [string]$step.target
            automation_name = [string]$step.automation_name
            caption = [string]$step.caption
            bounds = $bounds
            bounds_space = 'frame'
            frame = $relative
        }) | Out-Null
    }
    $journal.events = @($events.ToArray())
    $journal.status = 'passed'
    $journal.error = $null
} catch {
    $journal.error = $_.Exception.Message
} finally {
    if ($null -ne $grab) {
        try {
            $grab.StandardInput.WriteLine('q')
            $grab.StandardInput.Close()
            if (-not $grab.WaitForExit(15000)) { $grab.Kill() }
        } catch {}
    }
    Write-Journal
}
if ($journal.status -ne 'passed') {
    Write-Output $journal.error
    exit 1
}
Write-Output ("Шаги выполнены: " + @($journal.events).Count)
