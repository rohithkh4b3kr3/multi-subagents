# multi-subagents

A one-command setup that makes [Claude Code](https://docs.claude.com/en/docs/claude-code) read less, so sessions last longer and cost less, without changing the quality of the code or answers.

It combines four open-source token-saving tools, gives each **one job** so they don't fight each other, and adds three role-based subagents that know which tool to use for what.

| Tool | Its one job here | Where it acts |
|---|---|---|
| [rtk](https://github.com/rtk-ai/rtk) | Compress shell output (`git`, tests, `ls`, `grep`, `docker`, ...) | Hook on Bash calls |
| [code-review-graph](https://github.com/tirth8205/code-review-graph) | Code graph: callers, callees, tests, blast radius of a change | MCP server |
| [token-savior](https://github.com/mibayy/token-savior) | Read or edit one symbol instead of a whole file; project memory | MCP server |
| [context-mode](https://github.com/mksglu/context-mode) | Keep big outputs (logs, pages) in a sandbox and search them; survive context compaction | Claude Code plugin |

## Why this exists

Most of a Claude Code bill is the conversation being re-read on every turn. The parts you can shrink are the *inputs that pile into that history*: shell output, whole-file reads, big logs, and exploring code by opening file after file. Each tool above attacks one of those.

Installing all four naively causes trouble, because three of them want to intercept Bash calls. This repo fixes that by assigning ownership:

- **rtk is the only Bash rewriter.** token-savior's own Bash rewriter and compactors are left **off**.
- context-mode only routes *large* output to its sandbox (it nudges, it does not rewrite commands).
- code-review-graph answers structure questions; token-savior answers "show me this symbol".

### The agents

Split by **role**, not by tool. A real task ("review this change") needs several tools at once, and every hand-off between agents costs tokens.

| Agent | Model | Use it for | Edits files |
|---|---|---|---|
| `lean-explorer` | haiku | "How does X work?", "where is Y used?", log triage | No |
| `lean-reviewer` | sonnet | Bugs and blast radius of a diff, before you commit or merge | No |
| `lean-coder` | sonnet | Edits, refactors, bug fixes in unfamiliar or multi-file code | Yes |

Subagents work in their own context. Their file reads never enter your main conversation, only their short answer does.

## Requirements

- Linux or macOS (tested on Ubuntu 24.04)
- [Claude Code](https://docs.claude.com/en/docs/claude-code) installed (`claude --version`)
- `python3` 3.11+ with `venv` (Ubuntu: `sudo apt install python3-venv`)
- `curl`
- Node.js (only for context-mode; skip it with `--skip ctx`)

## Install

```bash
git clone https://github.com/rohithkh4b3kr3/multi-subagents
cd multi-subagents
./install.sh --dry-run     # see exactly what it will do, changes nothing
./install.sh               # asks once, then installs
```

Options:

```bash
./install.sh -y                       # no confirmation prompt
./install.sh --skip rtk,ctx           # skip components: rtk | crg | ts | ctx | agents
WORKSPACE_ROOTS=~/code ./install.sh   # folders token-savior may index (default: $HOME)
```

The installer backs up `~/.claude/settings.json` and `~/.claude.json` first, and is safe to re-run.

**Then restart Claude Code** (agents, hooks and MCP servers load at startup).

### Check it worked

```bash
claude mcp list          # code-review-graph, token-savior, plugin:context-mode all "Connected"
rtk gain                 # savings dashboard (empty until you run some commands)
```

Inside Claude Code run `/context-mode:ctx-doctor`; every line should pass. To confirm the shell hook, run `git status` in a repo and check that `rtk gain` counts it.

## Use

Just work as usual: rtk and context-mode act automatically. For the agents, name them:

```
use lean-explorer to explain how authentication works in this repo
use lean-reviewer to review my uncommitted changes
use lean-coder to rename the User.fullname field everywhere and fix the tests
```

The first time in a repo, the graph is built on demand (`Build the code review graph for this project` also works). It updates incrementally after that.

## Measure it

Don't trust the vendors' percentages; measure your own usage:

```bash
token-report --days 7      # cache re-reads, tool-output share, top commands, MCP tool calls
token-report --sessions    # per-session table
rtk gain                   # shell-output savings
```
in Claude Code: `/context-mode:ctx-stats` shows sandbox savings.

`token-report` reads only your local `~/.claude/projects/*.jsonl` transcripts and sends nothing anywhere. If it says "MCP tool calls: none", the model is not actually using the tools, which is worth investigating before you believe any savings.

## Honest expectations

- The headline numbers ("up to 90%", "65x fewer tokens") apply to raw tool output, which is only *part* of your usage. Cache re-reads of the conversation dominate, and none of these tools shrink that.
- On the author's own history (mostly setup and troubleshooting, not heavy coding) the total saving was estimated at **roughly 2-5%**. Coding sessions on large repos with lots of test, log and search output are where **15-35%** is plausible.
- The MCP servers add tool definitions to each request. On tiny tasks that overhead can cancel the gain.
- token-savior's published benchmark is unverified (its README says so). Keep it only if `token-report` shows it being used; otherwise remove it (`./install.sh --skip ts` on a fresh install, or `claude mcp remove -s user token-savior`).
- The biggest saving is free: run `/clear` between unrelated tasks, and `/compact <focus>` in long ones.

## Security and trust

This installs third-party software that **adds hooks to every Claude Code session** and runs local servers. Read the [install script](install.sh) and skim each upstream repo before running it. The rtk installer verifies the release checksum. Nothing here sends your code anywhere by itself, but check each project's own policies.

## Uninstall

```bash
./uninstall.sh
```

Removes the plugin, MCP servers, rtk hook, agents and `token-report`. The `rtk` binary stays in `~/.local/bin`; delete it if you want. Backups from install time are `~/.claude/settings.json.bak-*` and `~/.claude.json.bak-*`.

## Files

```
agents/lean-coder.md      edit agent (sonnet)
agents/lean-explorer.md   read-only exploration agent (haiku)
agents/lean-reviewer.md   read-only review agent (sonnet)
bin/token-report          usage report from local transcripts
install.sh / uninstall.sh
```

## Credits

All the heavy lifting is done by the upstream projects: [rtk](https://github.com/rtk-ai/rtk), [code-review-graph](https://github.com/tirth8205/code-review-graph), [token-savior](https://github.com/mibayy/token-savior) and [context-mode](https://github.com/mksglu/context-mode). This repo only wires them together and adds the agents.
