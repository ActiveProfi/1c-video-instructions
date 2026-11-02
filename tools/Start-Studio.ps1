$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$project = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$profile = Get-Content -LiteralPath (Join-Path $project 'config/target.json') -Raw | ConvertFrom-Json
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Студия видеоинструкций 1С'
$form.Size = New-Object System.Drawing.Size(800, 730)
$form.MinimumSize = $form.Size
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object System.Drawing.Font('Segoe UI', 11)
$heading = New-Object System.Windows.Forms.Label
$heading.Text = "Учебная 1С • $($profile.platform_version) • Бухгалтерия $($profile.configuration_version)"
$heading.SetBounds(20, 20, 745, 35)
$form.Controls.Add($heading)
$instructions = New-Object System.Windows.Forms.Label
$instructions.Text = 'Откройте документ в учебной базе. Сначала проверьте доступ к интерфейсу, затем управление. Документ не будет изменён.'
$instructions.SetBounds(20, 65, 745, 65)
$form.Controls.Add($instructions)
$inspect = New-Object System.Windows.Forms.Button
$inspect.Text = '1. Проверить доступ к 1С'
$inspect.SetBounds(20, 140, 350, 50)
$focus = New-Object System.Windows.Forms.Button
$focus.Text = '2. Проверить управление'
$focus.SetBounds(395, 140, 350, 50)
$form.Controls.AddRange(@($inspect, $focus))
$record = New-Object System.Windows.Forms.Button
$record.Text = '3. Записать демонстрацию (12 секунд)'
$record.SetBounds(20, 200, 725, 50)
$form.Controls.Add($record)
$overview = New-Object System.Windows.Forms.Button
$overview.Text = '4. Открыть готовую видеоинструкцию'
$overview.SetBounds(20, 260, 725, 50)
$overview.Add_Click({
    try {
        $report = Get-Content -LiteralPath (Join-Path $project 'artifacts/books-creation/video-result.json') -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($report.status -ne 'decode_verified' -or !(Test-Path -LiteralPath $report.video)) { throw 'Готовое видео пока не найдено.' }
        $videoPath = $report.video
        $videoStart = New-Object System.Diagnostics.ProcessStartInfo
        $videoStart.FileName = $videoPath
        $videoStart.UseShellExecute = $true
        [System.Diagnostics.Process]::Start($videoStart) | Out-Null
        $voiceName = @($voiceCatalog.voices | Where-Object { $_.id -eq $report.voice } | ForEach-Object name) -join ', '
        if (!$voiceName) { $voiceName = $report.voice }
        $log.Text = "Открыта последняя готовая видеоинструкция. Голос: $voiceName.`r`n$videoPath"
    } catch { $log.Text = $_.Exception.Message }
})
$form.Controls.Add($overview)
$voiceCatalog = Get-Content -LiteralPath (Join-Path $project 'config/voices.json') -Raw -Encoding UTF8 | ConvertFrom-Json
$voiceLabel = New-Object System.Windows.Forms.Label
$voiceLabel.Text = 'Голос следующей озвучки:'
$voiceLabel.SetBounds(20, 325, 220, 30)
$voiceChoice = New-Object System.Windows.Forms.ComboBox
$voiceChoice.DropDownStyle = 'DropDownList'
$voiceChoice.SetBounds(250, 320, 280, 35)
$voiceChoice.DisplayMember = 'name'
foreach ($entry in @($voiceCatalog.voices | Where-Object preferred)) { [void]$voiceChoice.Items.Add($entry) }
$lessonSpec = Get-Content -LiteralPath (Join-Path $project 'scenarios/books-creation-video.json') -Raw -Encoding UTF8 | ConvertFrom-Json
for ($i = 0; $i -lt $voiceChoice.Items.Count; $i++) {
    if ($voiceChoice.Items[$i].id -eq $lessonSpec.voice) { $voiceChoice.SelectedIndex = $i }
}
$saveVoice = New-Object System.Windows.Forms.Button
$saveVoice.Text = 'Сохранить голос'
$saveVoice.SetBounds(550, 320, 195, 35)
$saveVoice.Add_Click({
    try {
        if ($null -eq $voiceChoice.SelectedItem) { throw 'Выберите голос из списка.' }
        $selectedName = & (Join-Path $PSScriptRoot 'Set-LessonVoice.ps1') -VoiceId $voiceChoice.SelectedItem.id
        $log.Text = "Сохранён голос: $selectedName. Он будет использован при следующей озвучке. Готовое видео не изменено."
    } catch { $log.Text = $_.Exception.Message }
})
$form.Controls.AddRange(@($voiceLabel, $voiceChoice, $saveVoice))
$log = New-Object System.Windows.Forms.TextBox
$log.Multiline = $true
$log.ReadOnly = $true
$log.ScrollBars = 'Vertical'
$log.SetBounds(20, 375, 725, 255)
$log.Anchor = 'Top,Bottom,Left,Right'
$log.Text = "Готово к проверке.`r`n`r`nКнопка 3 записывает техническую демонстрацию. Кнопка 4 открывает пошаговую видеоинструкцию создания поступления книг по УПД.`r`n`r`nЦелый урок по теме собирается отдельно. Без живой базы: «Собрать учебный урок.cmd». В открытой учебной копии 1С: «Записать урок в 1С.cmd»."
$form.Controls.Add($log)
$footer = New-Object System.Windows.Forms.Label
$footer.Text = 'Результаты сохраняются в папке artifacts. Разработчик может прочитать их из проекта.'
$footer.SetBounds(20, 640, 725, 45)
$footer.Anchor = 'Bottom,Left,Right'
$form.Controls.Add($footer)
function Invoke-Probe([string]$ScriptName, [string]$ReportName) {
    $inspect.Enabled = $false
    $focus.Enabled = $false
    $record.Enabled = $false
    $form.UseWaitCursor = $true
    $log.Text = 'Выполняется проверка…'
    $log.Refresh()
    try {
        $reportPath = Join-Path $project "artifacts/$ReportName"
        # Run in the same interactive desktop session. Child exit does not close the UI.
        $scriptPath = Join-Path $PSScriptRoot $ScriptName
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -OutputPath "{1}"' -f $scriptPath, $reportPath
        $startedAt = Get-Date
        if ($ScriptName -eq 'Record-1CDemo.ps1') { $form.WindowState = 'Minimized' }
        $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -WindowStyle Hidden -PassThru
        $deadline = (Get-Date).AddSeconds(45)
        while (!$process.HasExited) {
            [System.Windows.Forms.Application]::DoEvents()
            if ((Get-Date) -gt $deadline) {
                $process.Kill()
                throw 'Проверка заняла слишком много времени. Закройте всплывающие окна 1С и повторите.'
            }
            Start-Sleep -Milliseconds 100
            $process.Refresh()
        }
        if (!(Test-Path -LiteralPath $reportPath)) { throw 'Отчёт не создан. Не удалось запустить проверку.' }
        if ((Get-Item -LiteralPath $reportPath).LastWriteTime -lt $startedAt) { throw 'Новый отчёт не создан. Предыдущий результат не используется.' }
        $report = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
        if ($ScriptName -eq 'Inspect-1C.ps1') {
            if ($report.status -eq 'no_visible_1c_window') {
                $log.Text = 'Окно 1С не найдено. Откройте учебную базу в этом сеансе Windows и повторите проверку.'
            } else {
                $fields = @($report.nodes | Where-Object { $_.type -in @('ControlType.Edit','ControlType.ComboBox') -and $_.name -and $_.enabled -and !$_.offscreen } | ForEach-Object name | Sort-Object -Unique)
                $log.Text = "Окно 1С доступно.`r`nНайдено элементов: $($report.node_count).`r`n`r`nДоступные поля:`r`n$($fields -join "`r`n")"
                if (!$fields.Count) { $log.AppendText("`r`nОткройте документ: поля документа пока не обнаружены.") }
                if ($report.possibly_truncated) { $log.AppendText("`r`nДерево интерфейса собрано не полностью.") }
                if ($report.errors.Count) { $log.AppendText("`r`nПри чтении некоторых элементов возникли ошибки.") }
            }
        } elseif ($ScriptName -eq 'Record-1CDemo.ps1') {
            if ($report.status -eq 'passed') {
                $log.Text = "Демонстрация записана: $($report.duration_seconds) секунд.`r`nТри поля выделены рамками и снабжены пояснениями.`r`nЗначения полей не изменились.`r`n`r`nВидео: $($report.video)`r`n`r`nФормат AVI, без озвучки."
            } else { $log.Text = "Запись остановлена: $($report.error)`r`nЕсли создан видеофайл, он содержит только частичный проход." }
        } elseif ($report.status -eq 'passed') {
            $log.Text = "Проверка управления пройдена.`r`nКурсор установлен в поле «Комментарий».`r`nТекст поля остался прежним. Документ не записывался."
        } else {
            $log.Text = "Управление пока не подтверждено.`r`nОставьте открытым один документ поступления, закройте всплывающие окна и повторите.`r`n`r`nТехническая причина: $($report.error)"
        }
        $log.AppendText("`r`n`r`nОтчёт сохранён: $ReportName")
    } catch { $log.Text = $_.Exception.Message }
    finally {
        $inspect.Enabled = $true
        $focus.Enabled = $true
        $record.Enabled = $true
        if ($ScriptName -eq 'Record-1CDemo.ps1') { $form.WindowState = 'Normal' }
        $form.UseWaitCursor = $false
    }
}
$inspect.Add_Click({ Invoke-Probe 'Inspect-1C.ps1' 'ui-probe.json' })
$focus.Add_Click({ Invoke-Probe 'Test-1CFocus.ps1' 'focus-test.json' })
$record.Add_Click({ Invoke-Probe 'Record-1CDemo.ps1' 'demo-result.json' })
[System.Windows.Forms.Application]::Run($form)
