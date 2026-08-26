[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$InboxDirectory = ".local/inbox/public-review",
    [string]$Remote = "origin",
    [string]$BaseBranch = "main",
    [string]$BranchPrefix = "automation/startup-sync",
    [switch]$SkipCandidateUpdate,
    [switch]$NoPullRequest
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$repositoryRoot = (Resolve-Path (Join-Path $scriptDirectory "..")).Path
$localDirectory = Join-Path $repositoryRoot ".local"
$logDirectory = Join-Path $localDirectory "logs"
$lockPath = Join-Path $localDirectory "startup-sync.lock"
$runId = [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss")
$logFile = Join-Path $logDirectory ("startup-sync-{0}.log" -f $runId)

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null

function Write-LocalLog {
    param([Parameter(Mandatory = $true)][string]$Message)

    $timestamp = [DateTime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ssZ")
    $line = "[$timestamp] $Message"
    Write-Host $line
    Add-Content -LiteralPath $logFile -Value $line -Encoding UTF8
}

function Require-Command {
    param([Parameter(Mandatory = $true)][string]$Name)

    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command is not available: $Name"
    }
}

function Invoke-NativeLogged {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [switch]$AllowFailure
    )

    & $FilePath @Arguments 2>&1 |
        ForEach-Object {
            Write-Host $_
            Add-Content -LiteralPath $logFile -Value $_ -Encoding UTF8
        }
    $exitCode = $LASTEXITCODE
    if (-not $AllowFailure -and $exitCode -ne 0) {
        throw "$FilePath exited with code $exitCode."
    }
    return $exitCode
}

function Resolve-LocalPath {
    param([Parameter(Mandatory = $true)][string]$PathValue)

    if ([System.IO.Path]::IsPathRooted($PathValue)) {
        return [System.IO.Path]::GetFullPath($PathValue)
    }
    return [System.IO.Path]::GetFullPath((Join-Path $repositoryRoot $PathValue))
}

function Assert-AllowedChangedPaths {
    param([Parameter(Mandatory = $true)][string[]]$Paths)

    $allowedExact = @(
        "content/chatgpt_scheduler_history.json",
        "site/data/latest.json",
        "site/data/i18n/en.json"
    )
    foreach ($path in $Paths) {
        $normalised = $path.Replace("\", "/")
        if ($allowedExact -contains $normalised) {
            continue
        }
        if ($normalised -match '^site/data/archive/[A-Za-z0-9][A-Za-z0-9._-]*\.json$') {
            continue
        }
        throw "Startup sync produced a non-allowlisted public change: $normalised"
    }
}

try {
    $lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
}
catch [System.IO.IOException] {
    Write-Host "Another startup sync is already running; skipping."
    exit 0
}

$worktreeDirectory = $null
$branchName = $null
$branchPushed = $false
$locationPushed = $false

try {
    Write-LocalLog "Starting local candidate refresh and reviewed-bundle sync."
    Require-Command "git"
    Require-Command $Python
    Require-Command "node"
    if (-not $NoPullRequest) {
        Require-Command "gh"
        & gh auth status *> $null
        if ($LASTEXITCODE -ne 0) {
            throw "GitHub CLI is not authenticated. Run 'gh auth login' locally."
        }
    }

    Push-Location $repositoryRoot
    $locationPushed = $true

    $insideWorkTree = & git rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -ne 0 -or $insideWorkTree.Trim() -ne "true") {
        throw "The script must run from a Git working copy."
    }

    $fetchSucceeded = $false
    for ($attempt = 1; $attempt -le 5; $attempt++) {
        Write-LocalLog "Fetching the public base branch (attempt $attempt of 5)."
        $fetchExit = Invoke-NativeLogged "git" @(
            "fetch", "--prune", $Remote, $BaseBranch
        ) -AllowFailure
        if ($fetchExit -eq 0) {
            $fetchSucceeded = $true
            break
        }
        Start-Sleep -Seconds ([Math]::Min(60, 10 * $attempt))
    }
    if (-not $fetchSucceeded) {
        throw "Unable to fetch the public base branch after five attempts."
    }

    if (-not $SkipCandidateUpdate) {
        Write-LocalLog "Refreshing local-only arXiv candidates."
        $candidateExit = Invoke-NativeLogged $Python @(
            "scripts/arxiv_digest.py",
            "--output",
            ".local/candidate-data/latest.json",
            "--archive-dir",
            ".local/candidate-data/archive"
        ) -AllowFailure
        if ($candidateExit -ne 0) {
            Write-LocalLog (
                "Candidate refresh failed, but an already-reviewed public bundle " +
                "may still be synchronized."
            )
        }
    }

    $resolvedInbox = Resolve-LocalPath $InboxDirectory
    $historyInbox = Join-Path $resolvedInbox "chatgpt_scheduler_history.json"
    $translationInbox = Join-Path $resolvedInbox "en.json"
    $historyExists = Test-Path -LiteralPath $historyInbox -PathType Leaf
    $translationExists = Test-Path -LiteralPath $translationInbox -PathType Leaf

    if (-not $historyExists -and -not $translationExists) {
        Write-LocalLog (
            "No reviewed public bundle is waiting. Candidate refresh is complete; " +
            "no branch or pull request was created."
        )
        exit 0
    }
    if ($historyExists -ne $translationExists) {
        throw (
            "The reviewed inbox is incomplete. It must contain both " +
            "chatgpt_scheduler_history.json and en.json."
        )
    }

    Write-LocalLog "Validating that the reviewed bundle is append-only and aligned."
    Invoke-NativeLogged $Python @(
        "scripts/validate_public_bundle.py",
        "--incoming-history", $historyInbox,
        "--incoming-translation", $translationInbox
    ) | Out-Null

    $safePrefix = $BranchPrefix.Trim().TrimEnd("/")
    if (-not $safePrefix -or $safePrefix -notmatch '^[A-Za-z0-9][A-Za-z0-9._/-]*$') {
        throw "BranchPrefix contains unsupported characters."
    }
    $branchName = "$safePrefix-$runId"
    $temporaryRoot = Join-Path ([System.IO.Path]::GetTempPath()) "public-page-startup-sync"
    New-Item -ItemType Directory -Force -Path $temporaryRoot | Out-Null
    $worktreeDirectory = Join-Path $temporaryRoot $runId

    Write-LocalLog "Creating an isolated temporary worktree and review branch."
    Invoke-NativeLogged "git" @(
        "worktree", "add", "-b", $branchName,
        $worktreeDirectory, "$Remote/$BaseBranch"
    ) | Out-Null

    Pop-Location
    $locationPushed = $false
    Push-Location $worktreeDirectory
    $locationPushed = $true

    Copy-Item -LiteralPath $historyInbox -Destination (
        Join-Path $worktreeDirectory "content/chatgpt_scheduler_history.json"
    ) -Force
    Copy-Item -LiteralPath $translationInbox -Destination (
        Join-Path $worktreeDirectory "site/data/i18n/en.json"
    ) -Force

    Write-LocalLog "Generating deterministic public artifacts."
    Invoke-NativeLogged $Python @("scripts/import_scheduler_history.py") | Out-Null

    $changedPaths = @(& git diff --name-only --)
    if ($LASTEXITCODE -ne 0) {
        throw "Unable to inspect generated changes."
    }
    if ($changedPaths.Count -eq 0) {
        throw "The reviewed bundle validated but produced no public changes."
    }
    Assert-AllowedChangedPaths $changedPaths

    Write-LocalLog "Running tests, artifact checks, and privacy validation."
    Invoke-NativeLogged $Python @(
        "-m", "unittest", "discover", "-s", "tests", "-v"
    ) | Out-Null
    Invoke-NativeLogged "node" @(
        "--test", "tests/test_model_math.cjs"
    ) | Out-Null
    Invoke-NativeLogged $Python @(
        "scripts/import_scheduler_history.py", "--check"
    ) | Out-Null
    Invoke-NativeLogged "git" @("diff", "--check") | Out-Null

    Invoke-NativeLogged "git" @(
        "add", "--",
        "content/chatgpt_scheduler_history.json",
        "site/data/latest.json",
        "site/data/archive",
        "site/data/i18n/en.json"
    ) | Out-Null

    $stagedPaths = @(& git diff --cached --name-only --)
    if ($LASTEXITCODE -ne 0 -or $stagedPaths.Count -eq 0) {
        throw "No reviewed public changes were staged."
    }
    Assert-AllowedChangedPaths $stagedPaths
    Invoke-NativeLogged "git" @("diff", "--cached", "--check") | Out-Null

    Write-LocalLog "Committing the reviewed public bundle on the automation branch."
    Invoke-NativeLogged "git" @(
        "commit", "-m", "content: sync reviewed scheduler archive"
    ) | Out-Null
    Invoke-NativeLogged "git" @(
        "push", "--set-upstream", $Remote, $branchName
    ) | Out-Null
    $branchPushed = $true

    if (-not $NoPullRequest) {
        $bodyPath = Join-Path $worktreeDirectory ".startup-sync-pr-body.md"
        @"
## What this PR does

- imports a complete reviewed scheduler-history snapshot;
- imports the matching reviewed English editorial overlay;
- regenerates deterministic public JSON archives;
- leaves `main` and GitHub Pages unchanged until this PR is reviewed and merged.

## Safety checks run locally

- append-only and immutable-edition validation;
- Japanese/English edition, paper, and rating-label alignment;
- full Python and JavaScript test suites;
- deterministic artifact check;
- allowlisted changed-path check;
- Git whitespace/error check.

Please review the public prose, dates, ratings, and privacy boundary before merging.
"@ | Set-Content -LiteralPath $bodyPath -Encoding UTF8

        Write-LocalLog "Opening a pull request for manual review."
        $prOutput = & gh pr create `
            --base $BaseBranch `
            --head $branchName `
            --title "content: sync reviewed scheduler archive" `
            --body-file $bodyPath 2>&1
        $prExit = $LASTEXITCODE
        $prOutput | ForEach-Object {
            Write-Host $_
            Add-Content -LiteralPath $logFile -Value $_ -Encoding UTF8
        }
        if ($prExit -ne 0) {
            throw "The branch was pushed, but GitHub CLI could not open the PR."
        }
    }

    $processedDirectory = Join-Path (
        Join-Path $localDirectory "processed/public-review"
    ) $runId
    New-Item -ItemType Directory -Force -Path $processedDirectory | Out-Null
    Move-Item -LiteralPath $historyInbox -Destination $processedDirectory
    Move-Item -LiteralPath $translationInbox -Destination $processedDirectory

    Write-LocalLog (
        "Startup sync completed. The reviewed bundle was moved to the local " +
        "processed archive; publication still requires PR review and merge."
    )
}
catch {
    Write-LocalLog "Startup sync failed safely: $($_.Exception.Message)"
    exit 1
}
finally {
    if ($locationPushed) {
        Pop-Location
    }
    if ($worktreeDirectory -and (Test-Path -LiteralPath $worktreeDirectory)) {
        Push-Location $repositoryRoot
        try {
            & git worktree remove --force $worktreeDirectory *> $null
        }
        finally {
            Pop-Location
        }
    }
    if ($branchName -and -not $branchPushed) {
        Push-Location $repositoryRoot
        try {
            & git branch -D $branchName *> $null
        }
        finally {
            Pop-Location
        }
    }
    $lockStream.Dispose()
}
