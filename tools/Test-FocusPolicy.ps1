$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'FocusPolicy.ps1')
$stable = Confirm-DemoFocus -Check { $true } -Restore { throw 'Unexpected restore' } -Delay {}
if ($stable -ne 0) { throw 'Stable focus should not require recovery' }
$state = @{ focused=$false; restores=0 }
$restored = Confirm-DemoFocus -Check { $state.focused } -Restore { $state.restores++; $state.focused=$true } -Delay {}
if ($restored -ne 1 -or $state.restores -ne 1) { throw 'Transient loss recovery failed' }
$state = @{ restores=0 }
$failed = $false
try { Confirm-DemoFocus -Check { $false } -Restore { $state.restores++ } -Delay {} | Out-Null } catch { $failed=$true }
if (!$failed -or $state.restores -ne 2) { throw 'Persistent loss must stop after two attempts' }
$state = @{ checks=0; restores=0 }
$failed = $false
try {
    Confirm-DemoFocus -Check { $state.checks++; ($state.checks % 2) -eq 0 } -Restore { $state.restores++ } -Delay {} | Out-Null
} catch { $failed=$true }
if (!$failed) { throw 'Unstable focus must not pass confirmation' }
'Focus policy tests passed: stable, recovered, persistent and unstable focus.'
