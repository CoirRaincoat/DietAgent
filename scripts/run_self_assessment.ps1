[CmdletBinding()]
param(
    [string]$ProjectName = "dietagent-self-assessment",
    [ValidateRange(1, 65535)]
    [int]$DemoPort = 8081,
    [string]$SettingsFile = "",
    [string]$PrivateDataDir = "",
    [string]$ProfilesFile = "",
    [string]$DialoguesFile = "",
    [string]$RecipesFile = "recipes_sample_2000.csv",
    [switch]$SkipBuild,
    [switch]$SkipUnitTests,
    [switch]$SkipPerformance
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "docker-compose.yml"
$envFile = Join-Path $repoRoot "configs\compose.env"
$runId = "{0}-{1}" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
$hostRunRoot = Join-Path $repoRoot "runtime\self_assessments\$runId"
$containerRunRoot = "/work/runtime/self_assessments/$runId"
$composeArgs = @(
    "compose",
    "--project-name", $ProjectName,
    "--env-file", $envFile,
    "--file", $composeFile
)

function Assert-LastExitCode {
    param([string]$Step)
    if ($LASTEXITCODE -ne 0) {
        throw "$Step failed with exit code $LASTEXITCODE."
    }
}

function Get-OnlyRunDirectory {
    param([string]$Parent)
    $directories = @(Get-ChildItem -LiteralPath $Parent -Directory -ErrorAction Stop)
    if ($directories.Count -ne 1) {
        throw "Expected one report directory under $Parent, found $($directories.Count)."
    }
    return $directories[0]
}

function Find-PrivateJsonFile {
    param(
        [string]$Directory,
        [ValidateSet("profiles", "dialogues")]
        [string]$Kind
    )
    $candidates = @()
    foreach ($file in Get-ChildItem -LiteralPath $Directory -Filter "*.json" -File) {
        try {
            $value = Get-Content -LiteralPath $file.FullName -Raw -Encoding UTF8 | ConvertFrom-Json
        }
        catch {
            continue
        }
        $expectedCount = if ($Kind -eq "profiles") { 50 } else { 20 }
        if ($value.Count -eq $expectedCount) {
            $candidates += $file
        }
    }
    if (-not $candidates) {
        throw "Could not auto-detect the $Kind JSON file in $Directory. Pass the filename explicitly."
    }
    if ($Kind -eq "profiles") {
        # Prefer the richer supplied profile file when both full and redacted sets exist.
        return $candidates | Sort-Object Length -Descending | Select-Object -First 1
    }
    if ($candidates.Count -ne 1) {
        throw "Found multiple 20-item JSON files in $Directory. Pass -DialoguesFile explicitly."
    }
    return $candidates[0]
}

function Import-ApplicationEnvironment {
    param([string]$Path)
    $allowed = @(
        "DEEPSEEK_API_KEY",
        "DEEPSEEK_BASE_URL",
        "DEEPSEEK_MODEL",
        "LLM_TIMEOUT_SECONDS"
    )
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }
        if ($trimmed.StartsWith("export ")) {
            $trimmed = $trimmed.Substring(7).TrimStart()
        }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) {
            continue
        }
        $name = $parts[0].Trim()
        if ($name -notin $allowed) {
            continue
        }
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        [Environment]::SetEnvironmentVariable($name, $value, "Process")
    }
}

New-Item -ItemType Directory -Force -Path $hostRunRoot | Out-Null
Push-Location $repoRoot
try {
    if (-not $SettingsFile) {
        $localSettings = Join-Path $repoRoot ".env"
        if (Test-Path -LiteralPath $localSettings -PathType Leaf) {
            $SettingsFile = $localSettings
        }
    }
    if ($SettingsFile) {
        $resolvedSettings = (Resolve-Path -LiteralPath $SettingsFile).Path
        Import-ApplicationEnvironment -Path $resolvedSettings
        Write-Host "Loaded model settings from the selected local file (values hidden)." -ForegroundColor DarkGray
    }
    if (-not $env:DEEPSEEK_API_KEY) {
        throw "No model API key is available. Add a local .env or pass -SettingsFile."
    }
    $env:COMPOSE_DISABLE_ENV_FILE = "1"

    Write-Host "[1/6] Checking Docker Engine..." -ForegroundColor Cyan
    & docker info --format "{{.ServerVersion}}"
    Assert-LastExitCode "Docker preflight"

    Write-Host "[2/6] Building and starting the current branch..." -ForegroundColor Cyan
    $env:DEMO_PORT = [string]$DemoPort
    $upArgs = @("up", "--detach")
    if (-not $SkipBuild) {
        $upArgs += "--build"
    }
    & docker @composeArgs @upArgs
    Assert-LastExitCode "Compose startup"

    Write-Host "[3/6] Waiting for model-enabled health check..." -ForegroundColor Cyan
    $health = $null
    for ($attempt = 1; $attempt -le 30; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "http://localhost:$DemoPort/health" -TimeoutSec 3
            if ($health.status -eq "ok") {
                break
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    if ($null -eq $health -or $health.status -ne "ok") {
        throw "Service did not become healthy at http://localhost:$DemoPort/health."
    }
    if ($health.llm_configured -ne $true) {
        throw "The service is healthy but no LLM is configured; model assessment would be invalid."
    }
    $probeBody = @{
        user_id = 900001
        message = "Plan one dinner for one person with three dishes and no soup."
        request_id = "preflight-$runId"
    } | ConvertTo-Json
    try {
        Invoke-RestMethod `
            -Uri "http://localhost:$DemoPort/chat" `
            -Method Post `
            -ContentType "application/json; charset=utf-8" `
            -Body $probeBody `
            -TimeoutSec 90 | Out-Null
    }
    catch {
        $details = if ($_.ErrorDetails.Message) { $_.ErrorDetails.Message } else { $_.Exception.Message }
        throw "Model preflight failed before scoring. Verify the API key, base URL and quota. $details"
    }

    if (-not $SkipUnitTests) {
        Write-Host "[4/6] Running deterministic unit and integration tests..." -ForegroundColor Cyan
        $unitArgs = @(
            "run", "--rm",
            "-v", "${repoRoot}:/work",
            "-w", "/work",
            "backend", "python", "-m", "pytest", "-q",
            "-p", "no:cacheprovider",
            "--basetemp", "/tmp/pytest-self-assessment"
        )
        & docker @composeArgs @unitArgs
        Assert-LastExitCode "Test suite"
    }
    else {
        Write-Warning "Unit tests were skipped. This weakens the assessment evidence."
    }

    Write-Host "[5/6] Running functional and streaming regression..." -ForegroundColor Cyan
    $regressionRoot = "$containerRunRoot/regression"
    $regressionArgs = @(
        "run", "--rm",
        "-v", "${repoRoot}:/work",
        "-w", "/work",
        "backend", "python", "-m", "evaluation.regression_suite",
        "--base-url", "http://frontend:8080",
        "--output-root", $regressionRoot
    )
    if ($SkipPerformance) {
        $regressionArgs += "--skip-performance"
    }
    & docker @composeArgs @regressionArgs
    $regressionExit = $LASTEXITCODE
    $regressionDirectory = Get-OnlyRunDirectory (Join-Path $hostRunRoot "regression")
    $regressionReport = "$regressionRoot/$($regressionDirectory.Name)/report.json"

    $privateExit = 0
    $privateReport = $null
    if ($PrivateDataDir) {
        $privateRoot = (Resolve-Path -LiteralPath $PrivateDataDir).Path
        if (-not $ProfilesFile) {
            $ProfilesFile = (Find-PrivateJsonFile -Directory $privateRoot -Kind "profiles").Name
        }
        if (-not $DialoguesFile) {
            $DialoguesFile = (Find-PrivateJsonFile -Directory $privateRoot -Kind "dialogues").Name
        }
        foreach ($file in @($ProfilesFile, $DialoguesFile, $RecipesFile)) {
            if (-not (Test-Path -LiteralPath (Join-Path $privateRoot $file) -PathType Leaf)) {
                throw "Missing private input file: $(Join-Path $privateRoot $file)"
            }
        }
        Write-Host "[5b/6] Running the optional private 50 x 20 matrix..." -ForegroundColor Cyan
        $privateOutputRoot = "$containerRunRoot/private"
        $privateArgs = @(
            "run", "--rm",
            "-v", "${repoRoot}:/work",
            "-v", "${privateRoot}:/private:ro",
            "-w", "/work",
            "backend", "python", "-m", "evaluation.private_matrix",
            "--profiles", "/private/$ProfilesFile",
            "--dialogues", "/private/$DialoguesFile",
            "--recipes", "/private/$RecipesFile",
            "--output-root", $privateOutputRoot
        )
        & docker @composeArgs @privateArgs
        $privateExit = $LASTEXITCODE
        $privateDirectory = Get-OnlyRunDirectory (Join-Path $hostRunRoot "private")
        $privateReport = "$privateOutputRoot/$($privateDirectory.Name)/report.json"
    }
    else {
        Write-Host "Private matrix skipped: provide -PrivateDataDir to include it." -ForegroundColor Yellow
    }

    Write-Host "[6/6] Consolidating regression gates and quality evidence..." -ForegroundColor Cyan
    $scorecardArgs = @(
        "run", "--rm",
        "-v", "${repoRoot}:/work",
        "-w", "/work",
        "backend", "python", "-m", "evaluation.scorecard",
        "--regression-report", $regressionReport,
        "--output-dir", $containerRunRoot
    )
    if ($privateReport) {
        $scorecardArgs += @("--private-report", $privateReport)
    }
    & docker @composeArgs @scorecardArgs
    $scorecardExit = $LASTEXITCODE

    $assessmentMarkdown = Join-Path $hostRunRoot "assessment.md"
    Write-Host ""
    Write-Host "Assessment report: $assessmentMarkdown" -ForegroundColor Green
    Get-Content -LiteralPath $assessmentMarkdown -Raw -Encoding UTF8

    if ($regressionExit -ne 0 -or $privateExit -ne 0 -or $scorecardExit -ne 0) {
        Write-Host "Self-assessment completed with failed or incomplete quality gates." -ForegroundColor Red
        exit 1
    }
}
finally {
    Pop-Location
}
