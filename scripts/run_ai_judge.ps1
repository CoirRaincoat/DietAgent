[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$BaselineReport,
    [Parameter(Mandatory = $true)]
    [string]$CandidateReport,
    [string]$SettingsFile = "",
    [string]$ProjectName = "dietagent-ai-judge",
    [string]$OutputDir = "",
    [switch]$SkipBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$composeFile = Join-Path $repoRoot "docker-compose.yml"
$envFile = Join-Path $repoRoot "configs\compose.env"

function Import-JudgeEnvironment {
    param([string]$Path)
    $allowed = @("JUDGE_API_KEY", "JUDGE_BASE_URL", "JUDGE_MODEL", "JUDGE_TIMEOUT_SECONDS")
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        if ($trimmed.StartsWith("export ")) { $trimmed = $trimmed.Substring(7).TrimStart() }
        $parts = $trimmed -split "=", 2
        if ($parts.Count -ne 2) { continue }
        $name = $parts[0].Trim()
        if ($name -notin $allowed) { continue }
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

if ($SettingsFile) {
    $resolvedSettings = (Resolve-Path -LiteralPath $SettingsFile).Path
    Import-JudgeEnvironment -Path $resolvedSettings
    Write-Host "Loaded judge settings from the selected local file (values hidden)." -ForegroundColor DarkGray
}
foreach ($name in @("JUDGE_API_KEY", "JUDGE_BASE_URL", "JUDGE_MODEL")) {
    if (-not [Environment]::GetEnvironmentVariable($name, "Process")) {
        throw "$name is required. Configure a separate OpenAI-compatible judge model."
    }
}

$baselinePath = (Resolve-Path -LiteralPath $BaselineReport).Path
$candidatePath = (Resolve-Path -LiteralPath $CandidateReport).Path
if ((Split-Path -Leaf $baselinePath) -ne "report.json" -or (Split-Path -Leaf $candidatePath) -ne "report.json") {
    throw "BaselineReport and CandidateReport must point to regression report.json files."
}
$baselineDir = Split-Path -Parent $baselinePath
$candidateDir = Split-Path -Parent $candidatePath
foreach ($directory in @($baselineDir, $candidateDir)) {
    if (-not (Test-Path -LiteralPath (Join-Path $directory "responses.jsonl") -PathType Leaf)) {
        throw "Missing responses.jsonl beside report: $directory"
    }
}

if (-not $OutputDir) {
    $runId = "{0}-{1}" -f [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssZ"), ([guid]::NewGuid().ToString("N").Substring(0, 8))
    $OutputDir = Join-Path $repoRoot "runtime\ai_judge\$runId"
}
$resolvedOutput = [System.IO.Path]::GetFullPath($OutputDir)
New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null

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
    $judgeEnvironmentArgs = @(
        "-e", "JUDGE_API_KEY", "-e", "JUDGE_BASE_URL", "-e", "JUDGE_MODEL"
    )
    if ($env:JUDGE_TIMEOUT_SECONDS) {
        $judgeEnvironmentArgs += @("-e", "JUDGE_TIMEOUT_SECONDS")
    }
    $runArgs = @("run", "--rm") + $judgeEnvironmentArgs + @(
        "-v", "${repoRoot}:/work:ro",
        "-v", "${baselineDir}:/judge/baseline:ro",
        "-v", "${candidateDir}:/judge/candidate:ro",
        "-v", "${resolvedOutput}:/judge/output",
        "-w", "/work", "backend", "python", "-m", "evaluation.ai_judge",
        "--baseline-report", "/judge/baseline/report.json",
        "--candidate-report", "/judge/candidate/report.json",
        "--output-dir", "/judge/output"
    )
    & docker @composeArgs @runArgs
    if ($LASTEXITCODE -ne 0) { throw "AI judge failed with exit code $LASTEXITCODE." }
    Write-Host "AI judge report: $(Join-Path $resolvedOutput 'report.md')" -ForegroundColor Green
    Get-Content -LiteralPath (Join-Path $resolvedOutput "report.md") -Raw -Encoding UTF8
}
finally {
    Pop-Location
}
