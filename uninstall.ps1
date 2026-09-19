<#
.SYNOPSIS
  Removes what install.ps1 added. Leaves your other Claude Code settings alone.
#>
$ErrorActionPreference = 'Continue'
$Home_ = $env:USERPROFILE
$Bin   = Join-Path $Home_ '.local\bin'
$env:Path = "$Bin;$env:Path"

if (Get-Command rtk -ErrorAction SilentlyContinue) { & rtk init -g --uninstall }
& claude plugin uninstall context-mode@context-mode 2>&1 | Out-Null
& claude plugin marketplace remove context-mode 2>&1 | Out-Null
& claude mcp remove -s user code-review-graph 2>&1 | Out-Null
& claude mcp remove -s user token-savior 2>&1 | Out-Null
foreach ($f in 'lean-coder.md', 'lean-explorer.md', 'lean-reviewer.md') { Remove-Item "$Home_\.claude\agents\$f" -ErrorAction SilentlyContinue }
Remove-Item "$Bin\token-report.py", "$Bin\token-report.cmd" -ErrorAction SilentlyContinue
Remove-Item "$Home_\.local\share\multi-subagents" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Removed. rtk.exe and the PATH entry ($Bin) are left in place; delete them yourself if you want."
Write-Host "Per-project graph data lives in each repo's .code-review-graph\ folder."
