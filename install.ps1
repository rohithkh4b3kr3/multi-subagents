<#
.SYNOPSIS
  multi-subagents installer for Windows (PowerShell 5.1+ or 7+).
  Wires rtk + code-review-graph + token-savior + context-mode into Claude Code
  (one job each) and installs three role-based subagents.

.EXAMPLE
  .\install.ps1 -DryRun                 # print what would happen, change nothing
  .\install.ps1                         # asks once, then installs
  .\install.ps1 -Yes -Skip rtk,ctx      # no prompt; skip components: rtk | crg | ts | ctx | cave | graphify | agents
  .\install.ps1 -DefaultAgent           # make lean-main your default agent in ~\.claude\settings.json
  .\install.ps1 -AutoHistory            # index past chats locally and auto-recall relevant notes into new chats
  .\install.ps1 -StatusLine             # show conversation size in Claude Code's status line (colour-coded)
  .\install.ps1 -TeamDir \\pc\share\usage -DeviceName alice-pc   # share this device's usage totals (numbers only), exported every 15 min
  .\install.ps1 -WorkspaceRoots C:\code # folders token-savior may index (default: your user folder)

  If scripts are blocked:  powershell -ExecutionPolicy Bypass -File .\install.ps1
  Prefer WSL? Run ./install.sh inside WSL instead (Linux instructions apply).
#>
param(
  [switch]$DryRun,
  [switch]$Yes,
  [switch]$DefaultAgent,
  [switch]$StatusLine,
  [switch]$AutoHistory,
  [string]$TeamDir = '',
  [string]$DeviceName = '',
  [string[]]$Skip = @(),
  [string]$WorkspaceRoots = $env:USERPROFILE
)
$ErrorActionPreference = 'Stop'

$Here   = Split-Path -Parent $MyInvocation.MyCommand.Path
$Home_  = $env:USERPROFILE
$Bin    = Join-Path $Home_ '.local\bin'
$Stack  = Join-Path $Home_ '.local\share\multi-subagents'
$Venv   = Join-Path $Stack 'venv'
$Agents = Join-Path $Home_ '.claude\agents'

function Want($name) { return -not ($Skip -contains $name) }
function Say($msg)   { Write-Host "`n== $msg" -ForegroundColor Cyan }
function Run([scriptblock]$Block, [string]$Label) {
  if ($DryRun) { Write-Host "   [dry-run] $Label"; return }
  $global:LASTEXITCODE = 0
  & $Block
  if ($LASTEXITCODE -gt 0) { throw "failed: $Label (exit $LASTEXITCODE)" }
}
# Run a native command whose failure or stderr output is expected and harmless
# (Windows PowerShell 5.1 turns native stderr into errors under -ErrorActionPreference Stop).
function Quiet([scriptblock]$Block) {
  $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
  try { & $Block 2>&1 | Out-Null } catch { } finally { $ErrorActionPreference = $old; $global:LASTEXITCODE = 0 }
}
function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

# ---- prerequisites -------------------------------------------------------
$missing = $false
if (-not (Have 'claude')) { Write-Warning 'missing: claude (install Claude Code first: https://docs.claude.com/en/docs/claude-code)'; $missing = $true }
$Py = $null
foreach ($c in @('python', 'py')) {
  if (Have $c) {
    $old = $ErrorActionPreference; $ErrorActionPreference = 'Continue'
    & $c -c "import sys, venv; sys.exit(0 if sys.version_info >= (3,11) else 1)" 2>&1 | Out-Null
    $good = ($LASTEXITCODE -eq 0)
    $ErrorActionPreference = $old
    if ($good) { $Py = $c; break }
  }
}
if (-not $Py -and ((Want 'crg') -or (Want 'ts'))) {
  Write-Warning 'need Python 3.11+ on PATH (https://www.python.org/downloads/ - tick "Add python.exe to PATH"), or use -Skip crg,ts'; $missing = $true
}
if ((Want 'ctx') -and -not (Have 'node')) { Write-Warning 'context-mode needs Node.js (https://nodejs.org), or use -Skip ctx'; $missing = $true }
if ($missing) { exit 1 }

Write-Host @"
This will:
  - back up ~\.claude\settings.json and ~\.claude.json
  - $(if (Want 'rtk')    { "download rtk (SHA-256 verified) to $Bin, add that folder to your USER PATH, and add a PreToolUse Bash hook to ~\.claude\settings.json" } else { '(skip rtk)' })
  - $(if (Want 'crg')    { "pip-install code-review-graph into $Venv and register it as a user MCP server" } else { '(skip code-review-graph)' })
  - $(if (Want 'ts')     { "pip-install token-savior into $Venv and register it (Bash rewriter OFF, roots: $WorkspaceRoots)" } else { '(skip token-savior)' })
  - $(if (Want 'ctx')    { 'install the context-mode Claude Code plugin (adds hooks)' } else { '(skip context-mode)' })
  - $(if (Want 'graphify') { "pip-install graphify into $Venv and register its /graphify skill (adds 3 lines to ~\.claude\CLAUDE.md)" } else { '(skip graphify)' })
  - $(if (Want 'cave')   { 'install the caveman plugin (shorter replies; changes how Claude writes, code stays exact)' } else { '(skip caveman)' })
  - $(if (Want 'agents') { "copy 4 agents to $Agents and token-report to $Bin" } else { '(skip agents)' })
$(if ($AutoHistory) { '  - build a local search index of your chat text (secrets removed) and add 2 hooks: index at session start, auto-recall of strong matches on prompts (max 3 short notes, 4 per session)' })
$(if ($StatusLine) { '  - set statusLine in ~\.claude\settings.json to show the conversation size (skipped if you already have one)' })
$(if ($TeamDir) { "  - export this device's per-day token totals (numbers and device name only) to $TeamDir every 15 minutes via a scheduled task" })
These are third-party tools that add hooks to every Claude Code session. Read the README first.
"@
if (-not $DryRun -and -not $Yes) {
  $a = Read-Host 'Continue? [y/N]'
  if ($a -notmatch '^[yY]') { Write-Host 'aborted'; exit 1 }
}

# ---- backups -------------------------------------------------------------
Say 'Backups'
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
foreach ($f in @("$Home_\.claude\settings.json", "$Home_\.claude.json")) {
  if (Test-Path $f) { Run { Copy-Item $f "$f.bak-$stamp" } "copy $f -> $f.bak-$stamp" }
}
Run { New-Item -ItemType Directory -Force -Path $Bin | Out-Null } "mkdir $Bin"
if (($env:Path -split ';') -notcontains $Bin) {
  $env:Path = "$Bin;$env:Path"
  $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
  if (($userPath -split ';') -notcontains $Bin) {
    Run { [Environment]::SetEnvironmentVariable('Path', "$Bin;$userPath", 'User') } "add $Bin to user PATH"
  }
}

# ---- rtk: the ONLY Bash rewriter ----------------------------------------
if (Want 'rtk') {
  Say 'rtk (shell output compression)'
  if ($DryRun) {
    Write-Host '   [dry-run] download latest rtk-x86_64-pc-windows-msvc.zip + checksums.txt, verify SHA-256, extract rtk.exe'
  } else {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $rel   = Invoke-RestMethod 'https://api.github.com/repos/rtk-ai/rtk/releases/latest' -Headers @{ 'User-Agent' = 'multi-subagents' }
    $asset = 'rtk-x86_64-pc-windows-msvc.zip'
    $tmp   = Join-Path ([IO.Path]::GetTempPath()) ("rtk-" + [guid]::NewGuid())
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $base  = "https://github.com/rtk-ai/rtk/releases/download/$($rel.tag_name)"
    Invoke-WebRequest "$base/$asset" -OutFile "$tmp\$asset" -UseBasicParsing
    Invoke-WebRequest "$base/checksums.txt" -OutFile "$tmp\checksums.txt" -UseBasicParsing
    $line = Select-String -Path "$tmp\checksums.txt" -Pattern ([regex]::Escape($asset) + '$') | Select-Object -First 1
    if (-not $line) { throw "checksum for $asset not found - refusing to install" }
    $expected = ($line.Line -split '\s+')[0].ToLower()
    $actual   = (Get-FileHash "$tmp\$asset" -Algorithm SHA256).Hash.ToLower()
    if ($expected -ne $actual) { throw "SHA-256 mismatch for $asset - refusing to install" }
    Write-Host '   checksum verified'
    Expand-Archive "$tmp\$asset" -DestinationPath "$tmp\x" -Force
    $exe = Get-ChildItem "$tmp\x" -Recurse -Filter rtk.exe | Select-Object -First 1
    if (-not $exe) { throw 'rtk.exe not found in archive' }
    Copy-Item $exe.FullName (Join-Path $Bin 'rtk.exe') -Force
    Remove-Item $tmp -Recurse -Force
  }
  Run { & rtk init -g --auto-patch } 'rtk init -g --auto-patch'
}

# ---- python MCP servers --------------------------------------------------
if ((Want 'crg') -or (Want 'ts') -or (Want 'graphify')) {
  Say "Python venv ($Venv)"
  Run { New-Item -ItemType Directory -Force -Path $Stack | Out-Null } "mkdir $Stack"
  Run { & $Py -m venv $Venv } "$Py -m venv $Venv"
  $pkgs = @()
  if (Want 'crg') { $pkgs += 'code-review-graph' }
  if (Want 'ts')  { $pkgs += 'token-savior-recall[mcp]' }
  if (Want 'graphify') { $pkgs += 'graphifyy' }
  Run { & "$Venv\Scripts\python.exe" -m pip install -q --disable-pip-version-check @pkgs } "pip install $($pkgs -join ' ')"
}
if (Want 'crg') {
  Say 'code-review-graph (structure: callers, blast radius, tests)'
  if (-not $DryRun) { Quiet { claude mcp remove -s user code-review-graph } }
  Run { & claude mcp add -s user code-review-graph -- "$Venv\Scripts\code-review-graph.exe" serve } 'claude mcp add code-review-graph'
}
if (Want 'ts') {
  Say 'token-savior (symbol-level reads/edits, memory)'
  if (-not $DryRun) { Quiet { claude mcp remove -s user token-savior } }
  # TS_BASH_COMPACT / TS_BASH_REWRITE are deliberately NOT set: rtk owns Bash.
  Run { & claude mcp add -s user token-savior -e TOKEN_SAVIOR_CLIENT=claude-code -e TOKEN_SAVIOR_PROFILE=optimized -e "WORKSPACE_ROOTS=$WorkspaceRoots" -- "$Venv\Scripts\token-savior.exe" } 'claude mcp add token-savior'
}

# ---- graphify (knowledge graph of code + docs, as a /graphify skill) -------
if (Want 'graphify') {
  Say 'graphify (whole-project and docs map; local, no API key)'
  Run { & "$Venv\Scripts\graphify.exe" install } 'graphify install'
}

# ---- context-mode plugin -------------------------------------------------
if (Want 'ctx') {
  Say 'context-mode (sandbox for big output, indexed search, session memory)'
  if (-not $DryRun) { Quiet { claude plugin marketplace add mksglu/context-mode } } else { Write-Host '   [dry-run] claude plugin marketplace add mksglu/context-mode' }
  Run { & claude plugin install context-mode@context-mode } 'claude plugin install context-mode@context-mode'
}

# ---- caveman plugin ------------------------------------------------------
if (Want 'cave') {
  Say 'caveman (terse replies: fewer output tokens)'
  if (-not $DryRun) { Quiet { claude plugin marketplace add JuliusBrussee/caveman } } else { Write-Host '   [dry-run] claude plugin marketplace add JuliusBrussee/caveman' }
  Run { & claude plugin install caveman@caveman } 'claude plugin install caveman@caveman'
}

# ---- agents + report script ---------------------------------------------
if (Want 'agents') {
  Say 'Subagents and token-report'
  Run { New-Item -ItemType Directory -Force -Path $Agents | Out-Null } "mkdir $Agents"
  foreach ($f in Get-ChildItem "$Here\agents\*.md") { Run { Copy-Item $f.FullName $Agents -Force } "copy $($f.Name) -> $Agents" }
  Run { Copy-Item "$Here\bin\token-report" (Join-Path $Bin 'token-report.py') -Force } "copy token-report.py -> $Bin"
  Run { Copy-Item "$Here\bin\token_data.py" (Join-Path $Bin 'token_data.py') -Force } "copy token_data.py -> $Bin"
  Run { Copy-Item "$Here\bin\token-statusline" (Join-Path $Bin 'token-statusline.py') -Force } "copy token-statusline.py -> $Bin"
  Run { Copy-Item "$Here\bin\token-history" (Join-Path $Bin 'token-history.py') -Force } "copy token-history.py -> $Bin"
  Run { New-Item -ItemType Directory -Force -Path "$Home_\.claude\commands" | Out-Null } 'mkdir commands'
  foreach ($f in Get-ChildItem "$Here\commands\*.md") { Run { Copy-Item $f.FullName "$Home_\.claude\commands" -Force } "copy $($f.Name) -> commands" }
  Run { Copy-Item "$Here\bin\token-dashboard" (Join-Path $Bin 'token-dashboard.py') -Force } "copy token-dashboard.py -> $Bin"
  $pyCmd = if ($Py) { $Py } else { 'python' }
  Run { Set-Content -Path (Join-Path $Bin 'token-history.cmd') -Value "@echo off`r`n$pyCmd `"%~dp0token-history.py`" %*" -Encoding ASCII } 'write token-history.cmd'
  Run { Set-Content -Path (Join-Path $Bin 'token-dashboard.cmd') -Value "@echo off`r`n$pyCmd `"%~dp0token-dashboard.py`" %*" -Encoding ASCII } 'write token-dashboard.cmd'
  # Start Menu shortcut "Token stack" (runs minimised so no console window lingers)
  Run { $lnkDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'; $w = New-Object -ComObject WScript.Shell; $l = $w.CreateShortcut((Join-Path $lnkDir 'Token stack.lnk')); $l.TargetPath = (Join-Path $Bin 'token-dashboard.cmd'); $l.WindowStyle = 7; $l.Description = 'Claude Code token usage'; $l.Save() } 'create Start Menu shortcut'
  Run { Set-Content -Path (Join-Path $Bin 'token-report.cmd') -Value "@echo off`r`n$pyCmd `"%~dp0token-report.py`" %*" -Encoding ASCII } 'write token-report.cmd'
}

if ($TeamDir) {
  Say 'Team usage sharing (numbers and device name only)'
  $exportArgs = @('--export', $TeamDir)
  if ($DeviceName) { $exportArgs += @('--name', $DeviceName) }
  Run { & (Join-Path $Bin 'token-report.cmd') @exportArgs } 'token-report --export'
  $tr = '"' + (Join-Path $Bin 'token-report.cmd') + '" --export "' + $TeamDir + '"' + $(if ($DeviceName) { ' --name "' + $DeviceName + '"' } else { '' })
  Run { schtasks /Create /F /SC MINUTE /MO 15 /TN 'multi-subagents token export' /TR $tr | Out-Null } 'schtasks create: export every 15 minutes'
}

# Add a top-level key to ~\.claude\settings.json only if it is missing (text insert keeps your formatting).
function Set-SettingIfMissing([string]$Key, [string]$JsonValue, [string]$Label) {
  $sp = Join-Path $Home_ '.claude\settings.json'
  if ($DryRun) { Write-Host "   [dry-run] set $Key in settings.json"; return }
  $entry = "`"$Key`": $JsonValue"
  if (-not (Test-Path $sp)) { Set-Content $sp "{`n  $entry`n}`n" -Encoding UTF8; Write-Host "   $Label set"; return }
  $txt = Get-Content $sp -Raw
  if ($txt -match ('"' + [regex]::Escape($Key) + '"\s*:')) { Write-Host "   settings.json already has $Key - left unchanged"; return }
  if ($txt -match '^\s*\{\s*\}\s*$') { Set-Content $sp "{`n  $entry`n}`n" -Encoding UTF8 }
  else { Set-Content $sp ([regex]::Replace($txt, '^\s*\{', { param($m) "{`n  $entry," }, 1)) -Encoding UTF8 }
  Write-Host "   $Label set"
}

if ($AutoHistory) {
  Say 'Local history memory (auto-index + auto-recall)'
  Run { & $Py (Join-Path $Bin 'token-history.py') index } 'token-history index'
  Run { & $Py (Join-Path $Bin 'token-history.py') hooks install } 'token-history hooks install'
  Write-Host '   check what it does any time: token-history stats   |   turn it off: token-history auto off'
}

if ($StatusLine) {
  Say 'Status line'
  $pyCmd2 = if ($Py) { $Py } else { 'python' }
  $cmdText = ($pyCmd2 + ' "' + (Join-Path $Bin 'token-statusline.py') + '"') -replace '\\', '/'
  Set-SettingIfMissing 'statusLine' ('{ "type": "command", "command": "' + ($cmdText -replace '"', '\"') + '" }') 'status line'
}

if ($DefaultAgent) {
  Say 'Default agent'
  Set-SettingIfMissing 'agent' '"lean-main"' 'default agent'
}

Say 'Done'
Write-Host 'Open a NEW terminal (PATH changed), restart Claude Code, then check:  claude mcp list   /context-mode:ctx-doctor   rtk gain'
Write-Host 'Try:  "use lean-explorer to explain how <something> works"'
Write-Host 'Desktop view:  token-dashboard   (or "Token stack" in the Start Menu)'
Write-Host 'After a week:  token-report --days 7'
