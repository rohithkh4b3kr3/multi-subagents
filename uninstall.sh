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
rm -f "$HOME/.claude/agents/lean-coder.md" "$HOME/.claude/agents/lean-explorer.md" \
      "$HOME/.claude/agents/lean-reviewer.md" "$HOME/.local/bin/token-report"
rm -rf "$STACK"
echo "Removed. The rtk binary (~/.local/bin/rtk) is left in place; delete it yourself if you want."
echo "Per-project graph data lives in each repo's .code-review-graph/ folder."
