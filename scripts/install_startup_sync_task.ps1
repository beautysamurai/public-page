[CmdletBinding()]
param(
    [string]$TaskName = "PublicPageStartupSync",
    [string]$Python = "python",
    [string]$DailyAt = "15:30",
    [switch]$StartupOnly,
    [switch]$RunNow,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ($Uninstall) {
    $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if ($existing) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        Write-Host "Removed scheduled task '$TaskName'."
    }
    else {
        Write-Host "Scheduled task '$TaskName' is not installed."
    }
    exit 0
}

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path (Join-Path $scriptDirectory "..")).Path
$syncScript = (Resolve-Path (Join-Path $scriptDirectory "sync_on_startup.ps1")).Path
$powerShellExecutable = (Get-Process -Id $PID).Path
$currentIdentity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

foreach ($command in @("git", "gh", "node")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is not available: $command"
    }
}

$pythonCommand = Get-Command `
    -Name $Python `
    -CommandType Application `
    -ErrorAction SilentlyContinue |
    Select-Object -First 1
if (-not $pythonCommand) {
    throw "Python executable is not available: $Python"
}
$Python = $pythonCommand.Path
try {
    & $Python --version *> $null
}
catch {
    throw "Python executable could not be started: $Python"
}
if ($LASTEXITCODE -ne 0) {
    throw "Python executable failed its version check: $Python"
}

& gh auth status *> $null
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run 'gh auth login' before installing the task."
}

try {
    $parsedDailyAt = [DateTime]::Today.Add([TimeSpan]::ParseExact(
        $DailyAt,
        "hh\:mm",
        [System.Globalization.CultureInfo]::InvariantCulture
    ))
}
catch {
    throw "DailyAt must use 24-hour HH:mm format, for example 09:30."
}

$quotedScript = '"{0}"' -f $syncScript.Replace('"', '\"')
$quotedPython = '"{0}"' -f $Python.Replace('"', '\"')
$arguments = (
    "-NoProfile -ExecutionPolicy Bypass -File {0} -Python {1}" -f
    $quotedScript,
    $quotedPython
)

$action = New-ScheduledTaskAction `
    -Execute $powerShellExecutable `
    -Argument $arguments `
    -WorkingDirectory $repositoryRoot

$triggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User $currentIdentity
)
if (-not $StartupOnly) {
    $triggers += New-ScheduledTaskTrigger -Daily -At $parsedDailyAt
}

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -WakeToRun `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1)

$principal = New-ScheduledTaskPrincipal `
    -UserId $currentIdentity `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $triggers `
    -Settings $settings `
    -Principal $principal `
    -Description (
        "Run the local OpenAI arXiv research pipeline and open a review PR only when a " +
        "complete sanitized public bundle is waiting in the local inbox."
    ) `
    -Force | Out-Null

Write-Host "Installed scheduled task '$TaskName'."
Write-Host "Triggers: at logon" -NoNewline
if (-not $StartupOnly) {
    Write-Host " and daily at $DailyAt."
}
else {
    Write-Host "."
}
Write-Host "Missed daily starts run when Windows next makes the task available."
Write-Host "The task never pushes to main; it creates a review branch and pull request."

if ($RunNow) {
    Start-ScheduledTask -TaskName $TaskName
    Write-Host "Started '$TaskName'. Check .local/logs for local output."
}
