<#
  Install the pinned DeepSeek Harness runtime used by AgentFence.

  Third-party MIT dependency: it stays under target/ and is never committed.
  The script installs @deepseek-ai/dsh@0.2.1-rc.2, seeds a DSH_HOME profile
  and, when the release binaries exist, runs the launcher --harness-check.
#>
param(
  [string]$HarnessDir = "target/harness",
  [string]$HarnessHome = "",
  [string]$Node = "",
  [string]$BinDir = "target/v2/release",
  [string]$Entry = "",
  [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$harness = if ([System.IO.Path]::IsPathRooted($HarnessDir)) { $HarnessDir } else { Join-Path $root $HarnessDir }
$homeDir = if (-not $HarnessHome) { Join-Path $harness "home" } elseif ([System.IO.Path]::IsPathRooted($HarnessHome)) { $HarnessHome } else { Join-Path $root $HarnessHome }
$entry = if ($Entry) { if ([System.IO.Path]::IsPathRooted($Entry)) { $Entry } else { Join-Path $root $Entry } } else { Join-Path $harness "node_modules/@deepseek-ai/dsh/lib/bin.js" }
$version = "0.2.1-rc.2"

if (-not $Node) {
  $cmd = Get-Command node -ErrorAction SilentlyContinue
  if (-not $cmd) { throw "node not found; pass -Node with an absolute path" }
  $Node = $cmd.Source
}
if (-not (Test-Path $Node)) { throw "node executable not found: $Node" }

if (-not $CheckOnly) {
  if (-not (Test-Path $entry)) {
    New-Item -ItemType Directory -Force -Path $harness | Out-Null
    Push-Location $harness
    try {
      npm install "@deepseek-ai/dsh@$version"
      if ($LASTEXITCODE -ne 0) { throw "npm install failed" }
    } finally { Pop-Location }
  }
  New-Item -ItemType Directory -Force -Path $homeDir | Out-Null
  $env:DSH_HOME = $homeDir
  & $Node $entry --profile acp --help | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Harness profile seeding failed" }
}

if (-not (Test-Path $entry)) { throw "Harness entry missing: $entry" }
$profile = Join-Path $homeDir "profiles/acp/package.json"
if (-not (Test-Path $profile)) { throw "Harness profile missing: $profile" }

Write-Host "harness: $entry"
Write-Host "home:    $homeDir"
$mcp = Join-Path $root (Join-Path $BinDir "agentfence-mcp-server.exe")
if (Test-Path $mcp) {
  $python = Join-Path $root "target/v2-venv/Scripts/python.exe"
  if (-not (Test-Path $python)) { $python = "python" }
  & $python -m demo.run_v2_dashboard --harness-check --harness-entry $entry --harness-node $Node --harness-home $homeDir --bin-dir $BinDir
  exit $LASTEXITCODE
}
Write-Host ("next: run target/v2-venv/Scripts/python.exe -m demo.run_v2_dashboard --harness-check --harness-entry {0} --harness-node {1} --harness-home {2} --bin-dir {3}" -f $entry, $Node, $homeDir, $BinDir)
