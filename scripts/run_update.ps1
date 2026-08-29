[CmdletBinding()]
param(
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path (Join-Path $scriptDirectory "..")).Path
$localDirectory = Join-Path $repositoryRoot ".local"
$logDirectory = Join-Path $localDirectory "logs"
$lockPath = Join-Path $localDirectory "run_update.lock"

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

try {
    $lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
}
catch [System.IO.IOException] {
    Write-Host "Another arXiv Daily update is already running; skipping."
    exit 0
}

$startedAt = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
$logFile = Join-Path $logDirectory (
    "update-{0}.log" -f [DateTime]::UtcNow.ToString("yyyyMMdd")
)
$locationPushed = $false

try {
    "[$startedAt] Starting automated arXiv research update." |
        Tee-Object -FilePath $logFile -Append

    Push-Location $repositoryRoot
    $locationPushed = $true

    $updaterArguments = @(
        "scripts/research_pipeline.py",
        "--config",
        "config/research.json",
        "--env-file",
        ".env",
        "daily",
        "--state",
        ".local/research/state.json",
        "--output-dir",
        ".local/research/daily"
    )
    & $Python @updaterArguments 2>&1 |
        Tee-Object -FilePath $logFile -Append

    if ($LASTEXITCODE -ne 0) {
        throw "Updater exited with code $LASTEXITCODE."
    }

    $finishedAt = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    "[$finishedAt] Research update completed under .local/research." |
        Tee-Object -FilePath $logFile -Append
}
catch {
    $failedAt = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    "[$failedAt] Update failed: $($_.Exception.Message)" |
        Tee-Object -FilePath $logFile -Append
    exit 1
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    $lockStream.Dispose()
}
