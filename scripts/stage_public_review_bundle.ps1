[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$HistoryPath,
    [Parameter(Mandatory = $true)][string]$TranslationPath,
    [string]$InboxDirectory = ".local/inbox/public-review",
    [string]$Python = "python"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path (Join-Path $scriptDirectory "..")).Path

function Resolve-InputFile {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    $resolved = Resolve-Path -LiteralPath $PathValue -ErrorAction Stop
    if (-not (Test-Path -LiteralPath $resolved.Path -PathType Leaf)) {
        throw "Input is not a file: $PathValue"
    }
    return $resolved.Path
}

function Resolve-LocalPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $PathValue))
}

if (-not (Get-Command $Python -ErrorAction SilentlyContinue)) {
    throw "Required Python command is not available: $Python"
}

$historySource = Resolve-InputFile $HistoryPath
$translationSource = Resolve-InputFile $TranslationPath
$resolvedInbox = Resolve-LocalPath $InboxDirectory
$inboxParent = Split-Path -Parent $resolvedInbox

if (Test-Path -LiteralPath $resolvedInbox) {
    throw (
        "A reviewed bundle is already waiting at the inbox. Process or remove " +
        "that bundle before staging another one."
    )
}

New-Item -ItemType Directory -Force -Path $inboxParent | Out-Null
$stageName = ".public-review-stage-{0}" -f [Guid]::NewGuid().ToString("N")
$stageDirectory = Join-Path $inboxParent $stageName
$locationPushed = $false

try {
    New-Item -ItemType Directory -Path $stageDirectory | Out-Null
    Copy-Item -LiteralPath $historySource -Destination (
        Join-Path $stageDirectory "chatgpt_scheduler_history.json"
    )
    Copy-Item -LiteralPath $translationSource -Destination (
        Join-Path $stageDirectory "en.json"
    )

    Push-Location $repositoryRoot
    $locationPushed = $true

    & $Python "scripts/validate_bundle_handoff.py" "create" `
        "--bundle-dir" $stageDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the producer completion manifest."
    }

    & $Python "scripts/validate_bundle_handoff.py" "validate" `
        "--bundle-dir" $stageDirectory
    if ($LASTEXITCODE -ne 0) {
        throw "The staged producer handoff failed validation."
    }

    Pop-Location
    $locationPushed = $false

    # The staging directory and final inbox share a parent. Moving the completed
    # directory into the absent inbox path is the producer-complete handoff.
    Move-Item -LiteralPath $stageDirectory -Destination $resolvedInbox
    $stageDirectory = $null

    Write-Host "Staged a producer-complete reviewed bundle at:"
    Write-Host $resolvedInbox
    Write-Host (
        "The startup sync may now atomically claim this directory and verify " +
        "the completion manifest before creating a pull request."
    )
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    if ($stageDirectory -and (Test-Path -LiteralPath $stageDirectory)) {
        Remove-Item -LiteralPath $stageDirectory -Recurse -Force
    }
}
