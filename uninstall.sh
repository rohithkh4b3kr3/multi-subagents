#!/usr/bin/env bash
# Removes what install.sh added. Leaves your other Claude Code settings alone.
set -uo pipefail
export PATH="$HOME/.local/bin:$PATH"
STACK="$HOME/.local/share/multi-subagents"

command -v rtk >/dev/null && rtk init -g --uninstall
claude plugin uninstall context-mode@context-mode 2>/dev/null
claude plugin marketplace remove context-mode 2>/dev/null
claude plugin uninstall caveman@caveman 2>/dev/null
claude plugin marketplace remove caveman 2>/dev/null
claude mcp remove -s user code-review-graph 2>/dev/null
claude mcp remove -s user token-savior 2>/dev/null
"$STACK/venv/bin/graphify" uninstall 2>/dev/null
python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.claude/settings.json")
if os.path.exists(p):
    d = json.load(open(p))
    changed = False
    if d.get("agent") == "lean-main":
        del d["agent"]; changed = True; print("removed default agent setting")
    if "token-statusline" in json.dumps(d.get("statusLine", "")):
        del d["statusLine"]; changed = True; print("removed status line setting")
    if changed:
        json.dump(d, open(p, "w"), indent=2); open(p, "a").write("\n")
PY
rm -f "$HOME/.claude/agents/lean-main.md" "$HOME/.claude/agents/lean-coder.md" "$HOME/.claude/agents/lean-explorer.md" \
      "$HOME/.claude/agents/lean-reviewer.md" "$HOME/.local/bin/token-report" \
      "$HOME/.local/bin/token_data.py" "$HOME/.local/bin/token-dashboard" "$HOME/.local/bin/token-statusline" "$HOME/.claude/commands/handoff.md" \
      "$HOME/.local/share/applications/token-dashboard.desktop" "$HOME/.local/share/icons/token-dashboard.svg"
rm -rf "$HOME/.cache/token-dashboard"
"$HOME/.local/bin/token-history" hooks remove 2>/dev/null
rm -f "$HOME/.local/bin/token-history" "$HOME/.claude/commands/recall.md"
rm -rf "$HOME/.claude/token-history"   # the local chat-text index
command -v crontab >/dev/null 2>&1 && crontab -l 2>/dev/null | grep -q "# multi-subagents" && { crontab -l | grep -v "# multi-subagents" | crontab -; echo "removed cron job"; }
rm -rf "$HOME/.config/token-stack"
rm -rf "$STACK"
echo "Removed. The rtk binary (~/.local/bin/rtk) is left in place; delete it yourself if you want."
echo "Per-project graph data lives in each repo's .code-review-graph/ folder."
