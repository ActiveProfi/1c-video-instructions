$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$form = New-Object Windows.Forms.Form
$form.Text = 'Подключение ElevenLabs'
$form.Size = New-Object Drawing.Size(640,310)
$form.StartPosition = 'CenterScreen'
$form.Font = New-Object Drawing.Font('Segoe UI',11)
$label = New-Object Windows.Forms.Label
$label.Text = "Вставьте API-ключ ElevenLabs. Он сохранится в файл .env проекта.`r`nЭто окно не отправляет текст и не создаёт озвучку."
$label.SetBounds(20,20,580,80)
$keyBox = New-Object Windows.Forms.TextBox
$keyBox.UseSystemPasswordChar = $true
$keyBox.SetBounds(20,110,580,32)
$save = New-Object Windows.Forms.Button
$save.Text = 'Сохранить ключ'
$save.SetBounds(20,160,220,40)
$status = New-Object Windows.Forms.Label
$status.SetBounds(20,210,580,45)
$save.Add_Click({
    try {
        $value = $keyBox.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($value)) { throw 'Вставьте ключ в поле выше.' }
        if ($value -match '[\r\n]') { throw 'Ключ должен быть одной строкой.' }
        $envPath = Join-Path $project '.env'
        $lines = @()
        if (Test-Path -LiteralPath $envPath) { $lines = @([IO.File]::ReadAllLines($envPath) | Where-Object { $_ -notmatch '^\s*ELEVENLABS_API_KEY\s*=' }) }
        $lines += 'ELEVENLABS_API_KEY=' + $value
        [IO.File]::WriteAllLines($envPath,$lines,(New-Object Text.UTF8Encoding($false)))
        $keyBox.Clear()
        $value = $null
        $status.Text = 'Ключ сохранён в .env. Закройте окно и напишите в чате: готово.'
    } catch { $status.Text = 'Не удалось сохранить ключ. Повторите ввод.' }
})
$form.Controls.AddRange(@($label,$keyBox,$save,$status))
$form.AcceptButton = $save
[Windows.Forms.Application]::Run($form)
