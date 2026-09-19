#!/usr/bin/env bash
# multi-subagents installer: wires rtk + code-review-graph + token-savior + context-mode
# into Claude Code (one job each) and installs three role-based subagents.
#
#   ./install.sh                 install everything (asks once before changing anything)
#   ./install.sh --dry-run       print what would happen, change nothing
#   ./install.sh -y              no confirmation prompt
#   ./install.sh --skip rtk,ctx  skip components: rtk | crg | ts | ctx | cave | graphify | agents
#   ./install.sh --default-agent make lean-main your default agent in ~/.claude/settings.json
#   ./install.sh --auto-history  index your past chats locally and auto-recall relevant notes into new chats
#   ./install.sh --statusline    show conversation size in Claude Code's status line (colour-coded)
#   ./install.sh --team-dir DIR [--device-name NAME]   share this device's usage totals (numbers only)
#                                with the people on your Claude account via folder DIR; exports every 15 min
#   WORKSPACE_ROOTS=~/code ./install.sh   folders token-savior may index (default: $HOME)
#
# Safe to re-run. Backs up ~/.claude/settings.json and ~/.claude.json first.
set -euo pipefail

DRY=0; YES=0; DEFAULT_AGENT=0; STATUSLINE=0; AUTO_HISTORY=0; TEAM_DIR=""; DEVICE_NAME=""; SKIP=","
while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run) DRY=1 ;;
    -y|--yes) YES=1 ;;
    --default-agent) DEFAULT_AGENT=1 ;;
    --statusline) STATUSLINE=1 ;;
    --auto-history) AUTO_HISTORY=1 ;;
    --team-dir) TEAM_DIR="$2"; shift ;;
    --device-name) DEVICE_NAME="$2"; shift ;;
    --skip) SKIP=",$2,"; shift ;;
    -h|--help) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PATH="$HOME/.local/bin:$PATH"
STACK="$HOME/.local/share/multi-subagents"
VENV="$STACK/venv"
ROOTS="${WORKSPACE_ROOTS:-$HOME}"

want() { case "$SKIP" in *",$1,"*) return 1 ;; *) return 0 ;; esac; }
say()  { printf '\n\033[1m== %s\033[0m\n' "$*"; }
run()  { if [ "$DRY" = 1 ]; then printf '   [dry-run] %s\n' "$*"; else "$@"; fi; }
need() { command -v "$1" >/dev/null 2>&1 || { echo "missing prerequisite: $1 ($2)" >&2; MISSING=1; }; }

# ---- prerequisites -------------------------------------------------------
MISSING=0
need claude  "install Claude Code first: https://docs.claude.com/en/docs/claude-code"
need python3 "sudo apt install python3"
need curl    "sudo apt install curl"
python3 -c 'import venv, sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null \
  || { echo "need python3 >= 3.11 with the venv module (Ubuntu: sudo apt install python3-venv)" >&2; MISSING=1; }
want ctx && need node "context-mode needs Node.js (sudo apt install nodejs), or run with --skip ctx"
if [ "$MISSING" != 0 ]; then
  if [ "$DRY" = 1 ]; then echo "(dry run: continuing despite the missing prerequisites above)"; else exit 1; fi
fi

cat <<EOF
This will:
  - back up ~/.claude/settings.json and ~/.claude.json
  - $(want rtk    && echo "install rtk to ~/.local/bin and add a PreToolUse Bash hook to ~/.claude/settings.json" || echo "(skip rtk)")
  - $(want crg    && echo "pip-install code-review-graph into $VENV and register it as a user MCP server" || echo "(skip code-review-graph)")
  - $(want ts     && echo "pip-install token-savior into $VENV and register it (Bash rewriter OFF, roots: $ROOTS)" || echo "(skip token-savior)")
  - $(want ctx    && echo "install the context-mode Claude Code plugin (adds hooks)" || echo "(skip context-mode)")
  - $(want graphify && echo "pip-install graphify into $VENV and register its /graphify skill (adds 3 lines to ~/.claude/CLAUDE.md)" || echo "(skip graphify)")
  - $(want cave   && echo "install the caveman plugin (shorter replies; changes how Claude writes, code stays exact)" || echo "(skip caveman)")
  - $(want agents && echo "copy 4 agents to ~/.claude/agents and token-report to ~/.local/bin" || echo "(skip agents)")
$([ "$AUTO_HISTORY" = 1 ] && echo "  - build a local search index of your chat text (secrets removed) and add 2 hooks: index at session start, auto-recall of strong matches on prompts (max 3 short notes, 4 per session)")
$([ "$STATUSLINE" = 1 ] && echo "  - set statusLine in ~/.claude/settings.json to show the conversation size (skipped if you already have one)")
$([ -n "$TEAM_DIR" ] && echo "  - export this device's per-day token totals (numbers + device name only) to $TEAM_DIR every 15 min via a user cron job")
$([ "$DEFAULT_AGENT" = 1 ] && echo "  - set \"agent\": \"lean-main\" in ~/.claude/settings.json (every session runs as lean-main)")
These are third-party tools that add hooks to every Claude Code session. Read the README first.
EOF
if [ "$DRY" = 0 ] && [ "$YES" = 0 ]; then
  read -r -p "Continue? [y/N] " a; [ "$a" = y ] || [ "$a" = Y ] || { echo "aborted"; exit 1; }
fi

# ---- backups -------------------------------------------------------------
say "Backups"
stamp=$(date -u +%Y%m%d-%H%M%S)
for f in "$HOME/.claude/settings.json" "$HOME/.claude.json"; do
  [ -f "$f" ] && run cp "$f" "$f.bak-$stamp"
done

# ---- rtk: the ONLY Bash rewriter ----------------------------------------
if want rtk; then
  say "rtk (shell output compression)"
  # Official installer; it verifies the release SHA-256 before installing.
  if [ "$DRY" = 1 ]; then echo "   [dry-run] curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh"
  else curl -fsSL https://raw.githubusercontent.com/rtk-ai/rtk/refs/heads/master/install.sh | sh; fi
  run rtk init -g --auto-patch
fi

# ---- python MCP servers --------------------------------------------------
if want crg || want ts || want graphify; then
  say "Python venv ($VENV)"
  run mkdir -p "$STACK"
  run python3 -m venv "$VENV"
  pkgs=()
  want crg && pkgs+=("code-review-graph")
  want ts  && pkgs+=("token-savior-recall[mcp]")
  want graphify && pkgs+=("graphifyy")
  run "$VENV/bin/pip" install -q --disable-pip-version-check "${pkgs[@]}"
fi
if want crg; then
  say "code-review-graph (structure: callers, blast radius, tests)"
  [ "$DRY" = 1 ] || claude mcp remove -s user code-review-graph >/dev/null 2>&1 || true
  run claude mcp add -s user code-review-graph -- "$VENV/bin/code-review-graph" serve
fi
if want ts; then
  say "token-savior (symbol-level reads/edits, memory)"
  [ "$DRY" = 1 ] || claude mcp remove -s user token-savior >/dev/null 2>&1 || true
  # TS_BASH_COMPACT / TS_BASH_REWRITE are deliberately NOT set: rtk owns Bash.
  run claude mcp add -s user token-savior \
    -e TOKEN_SAVIOR_CLIENT=claude-code -e TOKEN_SAVIOR_PROFILE=optimized \
    -e "WORKSPACE_ROOTS=$ROOTS" -- "$VENV/bin/token-savior"
fi

# ---- graphify (knowledge graph of code + docs, as a /graphify skill) -------
if want graphify; then
  say "graphify (whole-project and docs map; local, no API key)"
  run "$VENV/bin/graphify" install
fi

# ---- context-mode plugin -------------------------------------------------
if want ctx; then
  say "context-mode (sandbox for big output, indexed search, session memory)"
  run claude plugin marketplace add mksglu/context-mode || echo "   (marketplace already added?)"
  run claude plugin install context-mode@context-mode
fi

# ---- caveman plugin ------------------------------------------------------
if want cave; then
  say "caveman (terse replies: fewer output tokens)"
  run claude plugin marketplace add JuliusBrussee/caveman || echo "   (marketplace already added?)"
  run claude plugin install caveman@caveman
fi

# ---- agents + report script ---------------------------------------------
if want agents; then
  say "Subagents and token-report"
  run mkdir -p "$HOME/.claude/agents" "$HOME/.local/bin"
  for f in "$HERE"/agents/*.md; do run cp "$f" "$HOME/.claude/agents/"; done
  run install -m 0755 "$HERE/bin/token-report" "$HOME/.local/bin/token-report"
  run install -m 0644 "$HERE/bin/token_data.py" "$HOME/.local/bin/token_data.py"
  run install -m 0755 "$HERE/bin/token-statusline" "$HOME/.local/bin/token-statusline"
  run install -m 0755 "$HERE/bin/token-history" "$HOME/.local/bin/token-history"
  run mkdir -p "$HOME/.claude/commands"
  for f in "$HERE"/commands/*.md; do run cp "$f" "$HOME/.claude/commands/"; done
  run install -m 0755 "$HERE/bin/token-dashboard" "$HOME/.local/bin/token-dashboard"
  # Desktop launcher (Linux): appears in the app menu as "Token stack"
  if [ "$(uname -s)" = Linux ]; then
    run mkdir -p "$HOME/.local/share/applications" "$HOME/.local/share/icons"
    run install -m 0644 "$HERE/assets/token-dashboard.svg" "$HOME/.local/share/icons/token-dashboard.svg"
    if [ "$DRY" = 1 ]; then echo "   [dry-run] write ~/.local/share/applications/token-dashboard.desktop"; else
      cat > "$HOME/.local/share/applications/token-dashboard.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Token stack
Comment=Claude Code token usage
Exec=$HOME/.local/bin/token-dashboard
Icon=$HOME/.local/share/icons/token-dashboard.svg
Terminal=false
Categories=Development;
DESKTOP
    fi
  fi
fi

if [ "$DEFAULT_AGENT" = 1 ]; then
  say "Default agent"
  if [ "$DRY" = 1 ]; then echo "   [dry-run] set agent=lean-main in ~/.claude/settings.json"; else
    python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.claude/settings.json")
d = json.load(open(p)) if os.path.exists(p) else {}
if d.get("agent") and d["agent"] != "lean-main":
    print("   settings.json already has agent =", d["agent"], "- left unchanged")
else:
    d["agent"] = "lean-main"
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w"), indent=2); open(p, "a").write("\n")
    print("   default agent set to lean-main")
PY
  fi
fi

if [ "$AUTO_HISTORY" = 1 ]; then
  say "Local history memory (auto-index + auto-recall)"
  run "$HOME/.local/bin/token-history" index
  run "$HOME/.local/bin/token-history" hooks install
  echo "   check what it does any time: token-history stats   |   turn it off: token-history auto off"
fi

if [ "$STATUSLINE" = 1 ]; then
  say "Status line"
  if [ "$DRY" = 1 ]; then echo "   [dry-run] set statusLine=token-statusline in ~/.claude/settings.json"; else
    python3 - <<'PY'
import json, os
p = os.path.expanduser("~/.claude/settings.json")
d = json.load(open(p)) if os.path.exists(p) else {}
if d.get("statusLine"):
    print("   settings.json already has a statusLine - left unchanged")
else:
    d["statusLine"] = {"type": "command", "command": os.path.expanduser("~/.local/bin/token-statusline")}
    os.makedirs(os.path.dirname(p), exist_ok=True)
    json.dump(d, open(p, "w"), indent=2); open(p, "a").write("\n")
    print("   status line set")
PY
  fi
fi

if [ -n "$TEAM_DIR" ]; then
  say "Team usage sharing"
  NAME_ARG=(); [ -n "$DEVICE_NAME" ] && NAME_ARG=(--name "$DEVICE_NAME")
  run "$HOME/.local/bin/token-report" --export "$TEAM_DIR" "${NAME_ARG[@]}"
  CRON_LINE="*/15 * * * * $HOME/.local/bin/token-report --export \"$TEAM_DIR\" ${DEVICE_NAME:+--name \"$DEVICE_NAME\"} >/dev/null 2>&1 # multi-subagents"
  if [ "$DRY" = 1 ]; then echo "   [dry-run] add cron line: $CRON_LINE"
  elif command -v crontab >/dev/null 2>&1; then
    ( crontab -l 2>/dev/null | grep -v "# multi-subagents"; echo "$CRON_LINE" ) | crontab - && echo "   cron job added (every 15 min); remove with ./uninstall.sh"
  else echo "   no crontab here; run this yourself periodically: token-report --export \"$TEAM_DIR\""; fi
fi

case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) echo "note: add ~/.local/bin to your PATH" ;; esac
say "Done"
echo "Restart Claude Code, then check:  claude mcp list   /context-mode:ctx-doctor   rtk gain"
echo "Try:  \"use lean-explorer to explain how <something> works\""
echo "Desktop view:  token-dashboard   (or \"Token stack\" in your app menu)"
echo "After a week:  token-report --days 7"
