[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Prepare", "Evaluate")]
    [string]$Mode,
    [string]$BaselineReport = "",
    [string]$CandidateReport = "",
    [string]$JudgeReport = "",
    [string]$ReviewDir = "",
    [string]$OutputDir = "",
    [string]$ProjectName = "dietagent-judge-calibration",
    [int]$MinCases = 5,
    [double]$MinAgreement = 0.7,
    [double]$MinKappa = 0.4,
    [switch]$SkipBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "docker-compose.yml"
$envFile = Join-Path $repoRoot "configs\compose.env"
$runId = "{0}-{1}" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"), ([guid]::NewGuid().ToString("N").Substring(0, 8))

function Resolve-ReportBundle {
    param([string]$ReportPath, [string]$Label)
    if (-not $ReportPath) { throw "$Label is required in $Mode mode." }
    $resolved = (Resolve-Path -LiteralPath $ReportPath).Path
    if ((Split-Path -Leaf $resolved) -ne "report.json") {
        throw "$Label must point to a report.json file."
    }
    $directory = Split-Path -Parent $resolved
    if (-not (Test-Path -LiteralPath (Join-Path $directory "responses.jsonl") -PathType Leaf)) {
        throw "Missing responses.jsonl beside ${Label}: $directory"
    }
    return $directory
}

$composeArgs = @(
    "compose", "--project-name", $ProjectName,
    "--env-file", $envFile, "--file", $composeFile
)

Push-Location $repoRoot
try {
    & docker info --format "{{.ServerVersion}}"
    if ($LASTEXITCODE -ne 0) { throw "Docker preflight failed." }
    if (-not $SkipBuild) {
        & docker @composeArgs build backend
        if ($LASTEXITCODE -ne 0) { throw "Backend image build failed." }
    }

    if ($Mode -eq "Prepare") {
        $baselineDir = Resolve-ReportBundle -ReportPath $BaselineReport -Label "BaselineReport"
        $candidateDir = Resolve-ReportBundle -ReportPath $CandidateReport -Label "CandidateReport"
        if (-not $ReviewDir) { $ReviewDir = Join-Path $repoRoot "runtime\human_review\$runId" }
        $resolvedReview = [System.IO.Path]::GetFullPath($ReviewDir)
        New-Item -ItemType Directory -Force -Path $resolvedReview | Out-Null
        $orderSeed = [guid]::NewGuid().ToString("N")
        $runArgs = @(
            "run", "--rm",
            "-v", "${repoRoot}:/work:ro",
            "-v", "${baselineDir}:/review/baseline:ro",
            "-v", "${candidateDir}:/review/candidate:ro",
            "-v", "${resolvedReview}:/review/output",
            "-w", "/work", "backend", "python", "-m", "evaluation.judge_calibration",
            "prepare",
            "--baseline-report", "/review/baseline/report.json",
            "--candidate-report", "/review/candidate/report.json",
            "--output-dir", "/review/output",
            "--order-seed", $orderSeed
        )
        & docker @composeArgs @runArgs
        if ($LASTEXITCODE -ne 0) { throw "Human review preparation failed with exit code $LASTEXITCODE." }
        Write-Host "Human review directory: $resolvedReview" -ForegroundColor Green
        Write-Host "Give reviewers only human_review_packet.jsonl, human_labels.csv and HUMAN_REVIEW.md." -ForegroundColor Yellow
        Write-Host "Keep human_blinding_key.json away from reviewers." -ForegroundColor Yellow
        return
    }

    if (-not $JudgeReport) { throw "JudgeReport is required in Evaluate mode." }
    if (-not $ReviewDir) { throw "ReviewDir is required in Evaluate mode." }
    $resolvedJudge = (Resolve-Path -LiteralPath $JudgeReport).Path
    $judgeDir = Split-Path -Parent $resolvedJudge
    $resolvedReview = (Resolve-Path -LiteralPath $ReviewDir).Path
    foreach ($name in @("human_blinding_key.json", "human_labels.csv")) {
        if (-not (Test-Path -LiteralPath (Join-Path $resolvedReview $name) -PathType Leaf)) {
            throw "Missing $name in ReviewDir: $resolvedReview"
        }
    }
    if (-not $OutputDir) { $OutputDir = Join-Path $repoRoot "runtime\judge_calibration\$runId" }
    $resolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
    New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
    $runArgs = @(
        "run", "--rm",
        "-v", "${repoRoot}:/work:ro",
        "-v", "${judgeDir}:/calibration/judge:ro",
        "-v", "${resolvedReview}:/calibration/review:ro",
        "-v", "${resolvedOutput}:/calibration/output",
        "-w", "/work", "backend", "python", "-m", "evaluation.judge_calibration",
        "evaluate",
        "--judge-report", "/calibration/judge/report.json",
        "--review-key", "/calibration/review/human_blinding_key.json",
        "--human-labels", "/calibration/review/human_labels.csv",
        "--output-dir", "/calibration/output",
        "--min-cases", $MinCases,
        "--min-agreement", $MinAgreement,
        "--min-kappa", $MinKappa
    )
    & docker @composeArgs @runArgs
    if ($LASTEXITCODE -ne 0) { throw "Judge calibration failed with exit code $LASTEXITCODE." }
    Write-Host "Calibration report: $(Join-Path $resolvedOutput 'report.md')" -ForegroundColor Green
    Get-Content -LiteralPath (Join-Path $resolvedOutput "report.md") -Raw -Encoding UTF8
}
finally {
    Pop-Location
}
