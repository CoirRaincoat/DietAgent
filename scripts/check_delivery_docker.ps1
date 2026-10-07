param([string]$DockerContext = "desktop-linux")

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $repoRoot
$commit = (& git rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw "Cannot identify code commit." }
& git diff --quiet HEAD --
if ($LASTEXITCODE -ne 0) { throw "Tracked changes present; seal a candidate first." }
$runId = [Guid]::NewGuid().ToString("N").Substring(0, 8)
$out = Join-Path $repoRoot "runtime/delivery-docker-$($commit.Substring(0, 7))-$runId"
New-Item -ItemType Directory -Path $out | Out-Null
$archive = Join-Path $out "candidate.zip"
& git archive --format=zip "--output=$archive" $commit
if ($LASTEXITCODE -ne 0) { throw "Source export failed." }
$source = Join-Path $out "build-source"
Expand-Archive -LiteralPath $archive -DestinationPath $source
$image = "dietagent:delivery-$($commit.Substring(0, 7))-$runId"
& docker --context $DockerContext version --format '{{.Server.Version}}'
if ($LASTEXITCODE -ne 0) { throw "Docker Engine unavailable. No build or model call started." }
& docker --context $DockerContext build --label "dietagent.code_commit=$commit" --iidfile (Join-Path $out "image-id.txt") --tag $image $source
if ($LASTEXITCODE -ne 0) { throw "Fresh candidate build failed. Evidence retained at $out" }
# No key/.env/private mount, no external container network, no existing volume or demo image.
& docker --context $DockerContext run --rm --network none `
    --mount "type=bind,source=$source,target=/check,readonly" `
    --mount "type=bind,source=$out,target=/evidence" `
    $image python /check/scripts/check_delivery_source.py `
    --archive /evidence/candidate.zip --commit $commit `
    --source-directory /app --container --output /evidence/native-http
if ($LASTEXITCODE -ne 0) { throw "Container check failed. Evidence retained at $out" }
Write-Host "Container startup/error-contract verification complete: $out/native-http/summary.json"
Write-Host "Paid calls: 0. Menu quality/live NLU/frontend/platform not tested."
Write-Host "Temporary test container auto-removed; source evidence and fresh image retained. Existing containers/volumes untouched."
