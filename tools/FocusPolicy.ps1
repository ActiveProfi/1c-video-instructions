function Confirm-DemoFocus {
    param(
        [scriptblock]$Check,
        [scriptblock]$Restore,
        [scriptblock]$Delay = { Start-Sleep -Milliseconds 200 },
        [int]$MaxAttempts = 2
    )
    if (& $Check) { return 0 }
    for ($attempt = 1; $attempt -le $MaxAttempts; $attempt++) {
        & $Restore
        & $Delay
        if (& $Check) {
            & $Delay
            if (& $Check) { return $attempt }
        }
    }
    throw 'Не удалось восстановить устойчивый фокус поля. Закройте всплывающие окна 1С и повторите запись.'
}
