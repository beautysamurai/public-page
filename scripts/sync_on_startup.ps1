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

function Assert-ExactBundleEntries {
    param([Parameter(Mandatory = $true)][string]$BundleDirectory)

    $expected = @(
        "chatgpt_scheduler_history.json",
        "en.json",
        "bundle.complete.json"
    )
    $actual = @(
        Get-ChildItem -LiteralPath $BundleDirectory -Force |
            ForEach-Object { $_.Name }
    )
    $missing = @($expected | Where-Object { $_ -notin $actual })
    $unknown = @($actual | Where-Object { $_ -notin $expected })
    if ($missing.Count -gt 0 -or $unknown.Count -gt 0) {
        throw (
            "Claimed bundle entries do not match the producer-complete " +
            "handoff contract. Missing=[$($missing -join ', ')]; " +
            "unknown=[$($unknown -join ', ')]."
        )
    }
}

function New-ImmutableBundleSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$ClaimedDirectory,
        [Parameter(Mandatory = $true)][string]$SnapshotDirectory
    )

    $names = @(
        "chatgpt_scheduler_history.json",
        "en.json",
        "bundle.complete.json"
    )
    New-Item -ItemType Directory -Path $SnapshotDirectory | Out-Null

    $inputStreams = @()
    $outputStreams = @()
    try {
        # Open every producer file with exclusive sharing before copying any
        # bytes. A writer that did not perform the completed-directory handoff
        # causes the run to fail closed.
        foreach ($name in $names) {
            $inputStreams += [System.IO.File]::Open(
                (Join-Path $ClaimedDirectory $name),
                [System.IO.FileMode]::Open,
                [System.IO.FileAccess]::Read,
                [System.IO.FileShare]::None
            )
        }
        foreach ($name in $names) {
            $outputStreams += [System.IO.File]::Open(
                (Join-Path $SnapshotDirectory $name),
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
        }
        for ($index = 0; $index -lt $names.Count; $index++) {
            $inputStreams[$index].CopyTo($outputStreams[$index])
            $outputStreams[$index].Flush($true)
        }
    }
    finally {
        foreach ($stream in $outputStreams) {
            if ($stream) { $stream.Dispose() }
        }
        foreach ($stream in $inputStreams) {
            if ($stream) { $stream.Dispose() }
        }
    }

    return [PSCustomObject]@{
        History = Join-Path $SnapshotDirectory "chatgpt_scheduler_history.json"
        Translation = Join-Path $SnapshotDirectory "en.json"
        Manifest = Join-Path $SnapshotDirectory "bundle.complete.json"
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
$claimedBundleDirectory = $null
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
    if (-not (Test-Path -LiteralPath $resolvedInbox -PathType Container)) {
        Write-LocalLog (
            "No producer-complete reviewed bundle is waiting. Candidate refresh " +
            "is complete; no branch or pull request was created."
        )
        exit 0
    }

    $requiredInboxPaths = @(
        (Join-Path $resolvedInbox "chatgpt_scheduler_history.json"),
        (Join-Path $resolvedInbox "en.json"),
        (Join-Path $resolvedInbox "bundle.complete.json")
    )
    $missingInboxFiles = @(
        $requiredInboxPaths |
            Where-Object { -not (Test-Path -LiteralPath $_ -PathType Leaf) }
    )
    if ($missingInboxFiles.Count -gt 0) {
        Write-LocalLog (
            "The reviewed inbox has not completed the producer handoff. Use " +
            "scripts/stage_public_review_bundle.ps1 so both JSON files and the " +
            "hash-bound completion manifest appear together. Nothing was claimed."
        )
        exit 0
    }

    # The producer creates a fresh sibling staging directory, writes both JSON
    # files and their hash-bound completion manifest, and only then renames that
    # directory to public-review. Claim the completed directory in one move.
    $inboxParent = Split-Path -Parent $resolvedInbox
    $claimRoot = Join-Path $inboxParent ".public-review-claimed"
    New-Item -ItemType Directory -Force -Path $claimRoot | Out-Null
    $claimedBundleDirectory = Join-Path $claimRoot $runId
    Write-LocalLog "Atomically claiming the producer-complete reviewed bundle."
    Move-Item -LiteralPath $resolvedInbox -Destination $claimedBundleDirectory
    Assert-ExactBundleEntries -BundleDirectory $claimedBundleDirectory

    # Verify the producer's hash-bound completion manifest before reading any
    # bytes into the publication snapshot.
    Write-LocalLog "Verifying the claimed completion manifest before snapshotting."
    Invoke-NativeLogged $Python @(
        "scripts/validate_bundle_handoff.py",
        "validate",
        "--bundle-dir", $claimedBundleDirectory
    ) | Out-Null

    $snapshotDirectory = Join-Path $claimedBundleDirectory ".immutable-snapshot"
    Write-LocalLog "Creating an exclusive immutable snapshot of the claimed files."
    $snapshot = New-ImmutableBundleSnapshot `
        -ClaimedDirectory $claimedBundleDirectory `
        -SnapshotDirectory $snapshotDirectory
    $historySnapshot = $snapshot.History
    $translationSnapshot = $snapshot.Translation

    # Revalidate the exact bytes that will be published. This second check
    # protects the handoff-to-snapshot boundary as well as the producer boundary.
    Write-LocalLog "Revalidating the immutable snapshot and manifest hashes."
    Invoke-NativeLogged $Python @(
        "scripts/validate_bundle_handoff.py",
        "validate",
        "--bundle-dir", $snapshotDirectory
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
        $worktreeDirectory, "FETCH_HEAD"
    ) | Out-Null

    Pop-Location
    $locationPushed = $false
    Push-Location $worktreeDirectory
    $locationPushed = $true

    $currentHistory = Join-Path -Path $worktreeDirectory -ChildPath "content/chatgpt_scheduler_history.json"
    $currentTranslation = Join-Path -Path $worktreeDirectory -ChildPath "site/data/i18n/en.json"

    Write-LocalLog (
        "Validating the immutable snapshot against the fetched public base."
    )
    Invoke-NativeLogged $Python @(
        "scripts/validate_public_bundle.py",
        "--current-history", $currentHistory,
        "--current-translation", $currentTranslation,
        "--incoming-history", $historySnapshot,
        "--incoming-translation", $translationSnapshot
    ) | Out-Null

    Copy-Item -LiteralPath $historySnapshot -Destination $currentHistory -Force
    Copy-Item -LiteralPath $translationSnapshot -Destination $currentTranslation -Force

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

- imports a producer-complete reviewed scheduler-history snapshot;
- imports the matching reviewed English editorial overlay;
- regenerates deterministic public JSON archives;
- leaves `main` and GitHub Pages unchanged until this PR is reviewed and merged.

## Safety checks run locally

- atomic producer staging and hash-bound completion manifest;
- manifest verification before snapshotting and revalidation afterward;
- atomic inbox claim and exclusive immutable snapshot;
- append-only and immutable-edition validation against the fetched public base;
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

    $processedRoot = Join-Path $localDirectory "processed/public-review"
    New-Item -ItemType Directory -Force -Path $processedRoot | Out-Null
    $processedDirectory = Join-Path $processedRoot $runId
    Move-Item -LiteralPath $claimedBundleDirectory -Destination $processedDirectory
    $claimedBundleDirectory = $null

    Write-LocalLog (
        "Startup sync completed. The manifest-verified snapshot was moved to the " +
        "local processed archive; publication still requires PR review and merge."
    )
}
catch {
    Write-LocalLog "Startup sync failed safely: $($_.Exception.Message)"
    if (
        $claimedBundleDirectory -and
        (Test-Path -LiteralPath $claimedBundleDirectory -PathType Container)
    ) {
        Write-LocalLog (
            "The claimed bundle remains in local processing storage for " +
            "inspection; none of its unvalidated bytes were published."
        )
    }
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
