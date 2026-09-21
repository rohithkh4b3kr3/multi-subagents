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
& claude plugin uninstall caveman@caveman 2>&1 | Out-Null
& claude plugin marketplace remove caveman 2>&1 | Out-Null
foreach ($c in 'model-architecture','tokenization','fine-tuning','mechanistic-interpretability','data-processing','post-training','safety-alignment','distributed-training','infrastructure','optimization','evaluation','inference-serving','mlops','agents','rag','prompt-engineering','observability','multimodal','emerging-techniques','autoresearch','ml-paper-writing','ideation','agent-native-research-artifact') {
  & claude plugin uninstall "$c@ai-research-skills" 2>&1 | Out-Null
}
& claude plugin marketplace remove ai-research-skills 2>&1 | Out-Null
& claude mcp remove -s user code-review-graph 2>&1 | Out-Null
& claude mcp remove -s user token-savior 2>&1 | Out-Null
& "$Home_\.local\share\multi-subagents\venv\Scripts\graphify.exe" uninstall 2>&1 | Out-Null
$sp = "$Home_\.claude\settings.json"
if ((Test-Path $sp) -and ((Get-Content $sp -Raw) -match '"agent"\s*:\s*"lean-main"')) {
  $c = (Get-Content $sp -Raw) -replace '\s*"agent"\s*:\s*"lean-main"\s*,?', ''
  Set-Content $sp $c -Encoding UTF8; Write-Host 'removed default agent setting (check settings.json is still valid JSON)'
}
if ((Test-Path $sp) -and ((Get-Content $sp -Raw) -match 'token-statusline')) { Write-Host 'note: remove the "statusLine" entry that points to token-statusline from settings.json by hand' }
$th = "$Bin\token-history.py"
if (Test-Path $th) { & python $th hooks remove 2>&1 | Out-Null }
Remove-Item "$Bin\token-history.py", "$Bin\token-history.cmd", "$Home_\.claude\commands\recall.md" -ErrorAction SilentlyContinue
Remove-Item "$Home_\.claude\token-history" -Recurse -Force -ErrorAction SilentlyContinue   # the local chat-text index
foreach ($f in 'lean-main.md', 'lean-coder.md', 'lean-explorer.md', 'lean-reviewer.md') { Remove-Item "$Home_\.claude\agents\$f" -ErrorAction SilentlyContinue }
Remove-Item "$Bin\token-report.py", "$Bin\token-report.cmd", "$Bin\token_data.py", "$Bin\token-dashboard.py", "$Bin\token-dashboard.cmd", "$Bin\token-statusline.py", "$Bin\dash.py", "$Bin\dash.cmd", "$Home_\.claude\commands\handoff.md" -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Token stack.lnk') -ErrorAction SilentlyContinue
Remove-Item "$Home_\.cache\token-dashboard" -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item (Join-Path $env:APPDATA 'token-stack') -Recurse -Force -ErrorAction SilentlyContinue
schtasks /Delete /TN "multi-subagents token export" /F 2>&1 | Out-Null
Remove-Item "$Home_\.local\share\multi-subagents" -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "Removed. rtk.exe and the PATH entry ($Bin) are left in place; delete them yourself if you want."
Write-Host "Per-project graph data lives in each repo's .code-review-graph\ folder."
