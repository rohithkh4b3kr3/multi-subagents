"""token_data: read Claude Code's local transcripts and aggregate token usage.

Shared by `token-report` (terminal) and `token-dashboard` (desktop window).
Standard library only. Reads ~/.claude/projects/**/*.jsonl; nothing is sent anywhere.

Two things this module gets right that a naive sum does not:
  * One API response is written as several transcript rows that repeat the same
    `usage`. Usage is therefore counted once per requestId (deduped across files,
    since resumed sessions can copy earlier history into a new file).
  * Tool results are deduped by tool_use_id for the same reason.
"""
import glob
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from datetime import date, datetime, timedelta, timezone

HOME = os.path.expanduser("~")
CLAUDE = os.path.join(HOME, ".claude")
ROOT = os.path.join(CLAUDE, "projects")
CONFIG_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~/.config"), "token-stack")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")
_NAME_BAD = re.compile(r"[^A-Za-z0-9._ -]")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_FILE_CACHE = {}          # path -> (size, mtime, parsed)
_DATE_CACHE = {}          # "YYYY-MM-DDTHH:MM" -> local "YYYY-MM-DD"
_SKIP_CMDS = {"cd", "export", "set", "echo", "true", "sleep", "source", ".", "unset", "exec", "env", "time", "sudo", "timeout",
              "for", "while", "if", "then", "do", "done", "fi", "else", "{", "}", "(", ")"}


def _local_date(ts):
    """ISO-8601 UTC timestamp -> local calendar date string."""
    key = ts[:16]
    hit = _DATE_CACHE.get(key)
    if hit is None:
        try:
            dt = datetime.strptime(key, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc).astimezone()
            hit = dt.strftime("%Y-%m-%d")
        except ValueError:
            hit = key[:10]
        _DATE_CACHE[key] = hit
    return hit


def _cmd_name(command):
    """First 'real' command word of a shell line (command names only, never arguments)."""
    for seg in command.replace("&&", ";").replace("||", ";").replace("|", ";").split(";"):
        words = seg.split()
        while words and "=" in words[0] and not words[0].startswith(("/", "./", "~")):
            words = words[1:]                       # leading VAR=value
        if not words:
            continue
        w = words[0].replace("\\", "/").rsplit("/", 1)[-1]
        if w.lower().endswith(".exe"):
            w = w[:-4]
        if w and w not in _SKIP_CMDS:
            return w[:32]
    return "(shell)"


def _parse_file(path):
    """One transcript -> small dict of requests/tools/etc., keyed by ids for later dedupe."""
    reqs, tools, bash, mcp, agents, skills = {}, {}, {}, {}, {}, {}
    first = last = cwd = None
    with open(path, encoding="utf-8", errors="ignore") as fh:
        lines = fh.readlines()
    for line in lines:
        try:
            r = json.loads(line)
        except ValueError:
            continue
        ts = r.get("timestamp")
        if not ts:
            continue
        first = first or ts
        last = ts
        cwd = cwd or r.get("cwd")
        m = r.get("message")
        if not isinstance(m, dict):
            continue
        c = m.get("content")
        u = m.get("usage")
        if u and m.get("role") == "assistant":
            rid = r.get("requestId") or r.get("uuid")
            reqs[rid] = (ts, u.get("input_tokens") or 0, u.get("cache_creation_input_tokens") or 0,
                         u.get("cache_read_input_tokens") or 0, u.get("output_tokens") or 0)
        if not isinstance(c, list):
            continue
        for x in c:
            t = x.get("type")
            if t == "tool_use":
                tid, name, inp = x.get("id"), x.get("name", "?"), x.get("input") or {}
                tools[tid] = [ts, name, 0]
                if name == "Bash":
                    bash[tid] = (ts, _cmd_name(str(inp.get("command") or "")))
                elif name.startswith("mcp__"):
                    parts = name.split("__")
                    mcp[tid] = (ts, parts[1] if len(parts) > 2 else name)
                elif name in ("Agent", "Task"):
                    agents[tid] = (ts, str(inp.get("subagent_type") or "general-purpose"))
                elif name == "Skill":
                    skills[tid] = (ts, str(inp.get("skill") or "?"))
            elif t == "tool_result":
                cc = x.get("content")
                if isinstance(cc, list):
                    cc = " ".join(y.get("text", "") for y in cc if isinstance(y, dict))
                tid = x.get("tool_use_id")
                if tid in tools:
                    tools[tid][2] = len(str(cc or ""))
                else:                                # result seen before its call (resumed copy)
                    tools[tid] = [ts, "?", len(str(cc or ""))]
    project = os.path.basename((cwd or "").replace("\\", "/").rstrip("/")) or None
    return {"reqs": reqs, "tools": tools, "bash": bash, "mcp": mcp, "agents": agents,
            "skills": skills, "first": first, "last": last, "project": project}


def _load(path):
    try:
        st = os.stat(path)
    except OSError:
        return None
    hit = _FILE_CACHE.get(path)
    if hit and hit[0] == st.st_size and hit[1] == st.st_mtime:
        return hit[2]
    parsed = _parse_file(path)
    _FILE_CACHE[path] = (st.st_size, st.st_mtime, parsed)
    return parsed


def scan(root=None):
    """Aggregate every transcript. Returns {"buckets": [...], "sessions": [...], "generated": ts}.

    A bucket is one (local date, project) pair:
      {d, p, req, in, cw, cr, out, tool:{name:[calls,chars]}, bash:{cmd:n}, mcp:{server:n},
       agent:{type:n}, skill:{name:n}}
    A session is one transcript file: {id, p, start, end, req, cr, cw, out}.
    """
    root = root or ROOT
    files = sorted(glob.glob(os.path.join(root, "**", "*.jsonl"), recursive=True))
    parsed = []
    for f in files:
        p = _load(f)
        if p and p["first"]:
            parsed.append((f, p))
    parsed.sort(key=lambda fp: fp[1]["first"])          # oldest first: the original owns a shared request

    seen_req, seen_tool = set(), set()
    buckets, sessions = {}, []

    def bucket(ts, proj):
        k = (_local_date(ts), proj)
        b = buckets.get(k)
        if b is None:
            b = buckets[k] = {"d": k[0], "p": proj, "req": 0, "in": 0, "cw": 0, "cr": 0, "out": 0,
                              "tool": {}, "bash": {}, "mcp": {}, "agent": {}, "skill": {}}
        return b

    for f, p in parsed:
        rel = os.path.relpath(f, root).split(os.sep)
        proj = p["project"] or (rel[0].strip("-").split("-")[-1] if rel else "?")
        s = {"id": os.path.basename(f)[:8], "p": proj, "start": p["first"], "end": p["last"],
             "req": 0, "in": 0, "cr": 0, "cw": 0, "out": 0, "sub": len(rel) > 2}
        for rid, (ts, i, cw, cr, out) in p["reqs"].items():
            if rid in seen_req:
                continue
            seen_req.add(rid)
            b = bucket(ts, proj)
            b["req"] += 1; b["in"] += i; b["cw"] += cw; b["cr"] += cr; b["out"] += out
            s["req"] += 1; s["in"] += i; s["cw"] += cw; s["cr"] += cr; s["out"] += out
        for tid, (ts, name, chars) in p["tools"].items():
            if tid in seen_tool:
                continue
            seen_tool.add(tid)
            b = bucket(ts, proj)
            e = b["tool"].setdefault(name, [0, 0])
            e[0] += 1; e[1] += chars
            for src, dst in ((p["bash"], "bash"), (p["mcp"], "mcp"), (p["agents"], "agent"), (p["skills"], "skill")):
                if tid in src:
                    label = src[tid][1]
                    b[dst][label] = b[dst].get(label, 0) + 1
        if s["req"]:
            sessions.append(s)
    return {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "buckets": list(buckets.values()), "sessions": sessions}


def in_range(date_str, start, end=None):
    return (not start or date_str >= start) and (not end or date_str <= end)


# --------------------------------------------------------------------------- team / devices
# Several people can share one Claude account. Each device exports its own per-day totals to a
# shared folder (any way of sharing a folder works); the dashboard merges them. Files from other
# devices are UNTRUSTED input: everything is type-checked and size-limited before use.

def load_config():
    try:
        with open(CONFIG_FILE, encoding="utf-8") as f:
            c = json.load(f)
        return c if isinstance(c, dict) else {}
    except (OSError, ValueError):
        return {}


def save_config(**kw):
    c = load_config()
    c.update({k: v for k, v in kw.items() if v is not None})
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(c, f, indent=2)
    return c


def device_name(override=None):
    n = override or os.environ.get("TOKEN_STACK_DEVICE") or load_config().get("device") or socket.gethostname() or "unknown"
    return _NAME_BAD.sub("_", str(n)).strip()[:40] or "unknown"


def _int(v):
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v < 10 ** 13 else 0


def _counts(d):
    if not isinstance(d, dict):
        return {}
    return {str(k)[:60]: _int(v) for k, v in list(d.items())[:100]}


def export(dirpath, name=None, with_projects=False, data=None):
    """Write this device's per-day totals to <dirpath>/<device>.json (atomic). Returns (path, days)."""
    data = data or scan()
    dev = device_name(name)
    merged = {}
    for b in data["buckets"]:
        key = (b["d"], b["p"] if with_projects else "")
        m = merged.setdefault(key, {"d": b["d"], "p": key[1], "req": 0, "in": 0, "cw": 0, "cr": 0, "out": 0,
                                    "agent": {}, "mcp": {}, "skill": {}})
        for k in ("req", "in", "cw", "cr", "out"):
            m[k] += b[k]
        for g in ("agent", "mcp", "skill"):
            for n, v in b[g].items():
                m[g][n] = m[g].get(n, 0) + v
    doc = {"schema": 1, "device": dev, "exported": datetime.now().astimezone().isoformat(timespec="seconds"),
           "projects_included": bool(with_projects), "buckets": sorted(merged.values(), key=lambda m: m["d"])}
    os.makedirs(dirpath, exist_ok=True)
    path = os.path.join(dirpath, re.sub(r"\s+", "-", dev) + ".json")
    fd, tmp = tempfile.mkstemp(dir=dirpath, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(doc, f)
    os.replace(tmp, path)
    return path, len(doc["buckets"])


def read_team(dirpath, skip_device=None):
    """Read other devices' exports. Returns [{device, exported, days:[{d,req,in,cw,cr,out}]}]."""
    out = []
    if not dirpath or not os.path.isdir(dirpath):
        return out
    for f in sorted(glob.glob(os.path.join(dirpath, "*.json")))[:50]:
        try:
            if os.path.getsize(f) > 5_000_000:
                continue
            with open(f, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError):
            continue
        if not isinstance(doc, dict) or doc.get("schema") != 1 or not isinstance(doc.get("buckets"), list):
            continue
        dev = _NAME_BAD.sub("_", str(doc.get("device", ""))).strip()[:40]
        if not dev or (skip_device and dev.lower() == skip_device.lower()):
            continue
        days = {}
        for b in doc["buckets"][:20000]:
            if not isinstance(b, dict) or not isinstance(b.get("d"), str) or not _DATE_RE.fullmatch(b["d"]):
                continue
            e = days.setdefault(b["d"], {"d": b["d"], "req": 0, "in": 0, "cw": 0, "cr": 0, "out": 0})
            for k in ("req", "in", "cw", "cr", "out"):
                e[k] += _int(b.get(k))
        out.append({"device": dev, "exported": str(doc.get("exported", ""))[:32], "days": sorted(days.values(), key=lambda x: x["d"])})
    return out


def local_days(data):
    """This device's live per-day totals in the same shape read_team() returns."""
    days = {}
    for b in data["buckets"]:
        e = days.setdefault(b["d"], {"d": b["d"], "req": 0, "in": 0, "cw": 0, "cr": 0, "out": 0})
        for k in ("req", "in", "cw", "cr", "out"):
            e[k] += b[k]
    return sorted(days.values(), key=lambda x: x["d"])


# --------------------------------------------------------------------------- stack status + payload
# Shared by token-dashboard (desktop window) and dash (terminal UI) so both show identical numbers.
_scan_lock = threading.Lock()
_rtk_cache = (0.0, None)


def _json_file(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rtk_summary():
    """`rtk gain` totals (all time), cached for 30s. None if rtk is not installed."""
    global _rtk_cache
    now = time.time()
    if now - _rtk_cache[0] < 30:
        return _rtk_cache[1]
    home = Path(HOME)
    exe = shutil.which("rtk") or next((str(p) for p in (home / ".local/bin/rtk", home / ".local/bin/rtk.exe") if p.exists()), None)
    out = None
    if exe:
        try:
            r = subprocess.run([exe, "gain", "--format", "json"], capture_output=True, text=True, timeout=8)
            out = json.loads(r.stdout).get("summary")
        except (OSError, ValueError, subprocess.SubprocessError):
            out = {}
    _rtk_cache = (now, out)
    return out


def history_status(settings):
    """token-history: installed? hooks on? and what has auto-recall injected so far."""
    d, home = Path(CLAUDE) / "token-history", Path(HOME)
    hooks = any("token-history" in h.get("command", "") for e in settings.get("hooks", {}).get("UserPromptSubmit", []) for h in e.get("hooks", []))
    out = {"installed": any((home / ".local/bin" / n).exists() for n in ("token-history", "token-history.py")),
           "hooks": hooks, "mode": _json_file(d / "config.json").get("mode", "recall"), "msgs": 0, "inject": 0, "inject_tokens": 0}
    try:
        con = sqlite3.connect(f"file:{d / 'history.db'}?mode=ro", uri=True, timeout=2)
        out["msgs"] = con.execute("SELECT count(*) FROM msgs").fetchone()[0]
        n, chars = con.execute("SELECT count(*), coalesce(sum(chars),0) FROM events WHERE kind='inject'").fetchone()
        out["inject"], out["inject_tokens"] = n, round(chars / 4)
        con.close()
    except sqlite3.Error:
        pass
    return out


def stack_status():
    """What is installed, read from config files (no servers are started)."""
    claude, home = Path(CLAUDE), Path(HOME)
    settings = _json_file(claude / "settings.json")
    hooks = settings.get("hooks", {}).get("PreToolUse", [])
    agents_dir = claude / "agents"
    return {
        "rtk_installed": rtk_summary() is not None,
        "rtk_hook": any("rtk hook" in h.get("command", "") for e in hooks for h in e.get("hooks", [])),
        "mcp": sorted(_json_file(home / ".claude.json").get("mcpServers", {})),
        "plugins": sorted(_json_file(claude / "plugins" / "installed_plugins.json").get("plugins", {})),
        "agents": sorted(p.stem for p in agents_dir.glob("*.md")) if agents_dir.is_dir() else [],
        "default_agent": settings.get("agent"),
        "graphify": (claude / "skills" / "graphify").exists(),
        "history": history_status(settings),
    }


def payload(team_dir=None):
    """Everything a UI needs: usage buckets, sessions, stack status, rtk savings and the team view."""
    with _scan_lock:
        d = scan()
    d["status"] = stack_status()
    d["rtk"] = rtk_summary() or {}
    me = device_name()
    team_dir = team_dir or load_config().get("team_dir")
    devices = [{"device": me, "local": True, "exported": "", "days": local_days(d)}]
    devices += [dict(t, local=False) for t in read_team(team_dir, skip_device=me)]
    d["team"] = {"dir": team_dir, "device": me, "devices": devices}
    return d


# --------------------------------------------------------------------------- themes
# One palette definition drives both the terminal UI (dash) and the desktop app (token-dashboard).
THEMES = {
    "tokyo-night":      {"label": "Tokyo Night",      "dark": True,  "bg": "#1a1b26", "surface": "#1f2335", "border": "#3b4261", "fg": "#c0caf5", "dim": "#8089b3",
                         "accent": "#7aa2f7", "accent2": "#bb9af7", "good": "#9ece6a", "warn": "#e0af68", "bad": "#f7768e", "cyan": "#7dcfff"},
    "catppuccin-mocha": {"label": "Catppuccin Mocha", "dark": True,  "bg": "#1e1e2e", "surface": "#181825", "border": "#45475a", "fg": "#cdd6f4", "dim": "#9399b2",
                         "accent": "#89b4fa", "accent2": "#cba6f7", "good": "#a6e3a1", "warn": "#f9e2af", "bad": "#f38ba8", "cyan": "#89dceb"},
    "dracula":          {"label": "Dracula",          "dark": True,  "bg": "#282a36", "surface": "#21222c", "border": "#44475a", "fg": "#f8f8f2", "dim": "#98a3cf",
                         "accent": "#bd93f9", "accent2": "#ff79c6", "good": "#50fa7b", "warn": "#f1fa8c", "bad": "#ff5555", "cyan": "#8be9fd"},
    "gruvbox-dark":     {"label": "Gruvbox Dark",     "dark": True,  "bg": "#282828", "surface": "#1d2021", "border": "#504945", "fg": "#ebdbb2", "dim": "#a89984",
                         "accent": "#83a598", "accent2": "#d3869b", "good": "#b8bb26", "warn": "#fabd2f", "bad": "#fb4934", "cyan": "#8ec07c"},
    "one-dark":         {"label": "One Dark",         "dark": True,  "bg": "#282c34", "surface": "#21252b", "border": "#3e4451", "fg": "#abb2bf", "dim": "#8b93a5",
                         "accent": "#61afef", "accent2": "#c678dd", "good": "#98c379", "warn": "#e5c07b", "bad": "#e06c75", "cyan": "#56b6c2"},
    "github-light":     {"label": "GitHub Light",     "dark": False, "bg": "#ffffff", "surface": "#f6f8fa", "border": "#d0d7de", "fg": "#1f2328", "dim": "#59636e",
                         "accent": "#0969da", "accent2": "#8250df", "good": "#1a7f37", "warn": "#9a6700", "bad": "#cf222e", "cyan": "#0a7ea4"},
}
DEFAULT_THEME = "tokyo-night"


def mix(a, b, t):
    """Blend hex colour a toward b by t (0..1); returns hex."""
    pa = [int(a[i:i + 2], 16) for i in (1, 3, 5)]
    pb = [int(b[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(pa, pb))


def theme_css():
    """CSS custom properties for every theme, for the desktop app."""
    out = []
    for name, th in THEMES.items():
        v = dict(th, track=mix(th["bg"], th["accent"], 0.16), hover=mix(th["accent"], th["fg"], 0.25), soft=mix(th["bg"], th["surface"], 0.5))
        body = ";".join(f"--{k}:{v[k]}" for k in ("bg", "surface", "border", "fg", "dim", "accent", "accent2", "good", "warn", "bad", "cyan", "track", "hover", "soft"))
        out.append(f':root[data-theme="{name}"]{{color-scheme:{"dark" if th["dark"] else "light"};{body}}}')
    return "\n".join(out)


# --------------------------------------------------------------------------- views
RANGES = ("today", "7", "30", "all")
RANGE_LABELS = {"today": "Today", "7": "7 days", "30": "30 days", "all": "All"}
RANK_METRICS = ("fresh", "cr", "req")
LARGE_CONTEXT = 100_000


def compact(n):
    """1234567 -> '1.23M'; whole numbers never lose zeros (220000 -> '220K')."""
    def f(v, u):
        x = f"{v:.0f}" if v >= 100 else f"{v:.1f}" if v >= 10 else f"{v:.2f}"
        if "." in x:
            x = x.rstrip("0").rstrip(".")
        return x + u
    n = float(n)
    if n >= 999.5e6:
        return f(n / 1e9, "B")
    if n >= 999.5e3:
        return f(n / 1e6, "M")
    if n >= 1e3:
        return f(n / 1e3, "K")
    return str(round(n))


def range_start(rng, today=None):
    if rng == "all":
        return None
    n = 1 if rng == "today" else int(rng)
    return ((today or date.today()) - timedelta(days=n - 1)).isoformat()


def aggregate(data, start=None, project=None):
    A = {"req": 0, "in": 0, "cw": 0, "cr": 0, "out": 0, "tool": {}, "bash": {}, "mcp": {}, "agent": {}, "skill": {}, "day": {}}
    for b in data["buckets"]:
        if (start and b["d"] < start) or (project and project != "all" and b["p"] != project):
            continue
        for k in ("req", "in", "cw", "cr", "out"):
            A[k] += b[k]
        for n, (c, ch) in b["tool"].items():
            e = A["tool"].setdefault(n, [0, 0]); e[0] += c; e[1] += ch
        for g in ("bash", "mcp", "agent", "skill"):
            for n, v in b[g].items():
                A[g][n] = A[g].get(n, 0) + v
        d = A["day"].setdefault(b["d"], {"req": 0, "cw": 0, "cr": 0, "out": 0})
        for k in ("req", "cw", "cr", "out"):
            d[k] += b[k]
    return A


def day_series(A, start=None, today=None):
    """Continuous per-day list from the range start (or first data day) to today; zero-filled."""
    days = sorted(A["day"])
    if not days:
        return []
    cur = date.fromisoformat(min(start, days[0]) if start else days[0])
    end = today or date.today()
    out = []
    while cur <= end and len(out) < 800:
        k = cur.isoformat()
        out.append(dict(A["day"].get(k, {"req": 0, "cw": 0, "cr": 0, "out": 0}), d=k))
        cur += timedelta(days=1)
    return out


def stack_rows(S, rtk, A):
    """One row per component: installed?, calls in range, and a verdict the UIs colour and label."""
    has = lambda lst, key: any(key in x for x in lst)
    calls = lambda obj, key: sum(v for n, v in obj.items() if key in n)
    H, rows = S["history"], []

    def add(name, job, installed, n=None, note="", verdict=None):
        if verdict is None:
            verdict = "missing" if not installed else "installed" if n is None else "used" if n > 0 else "unused"
        rows.append({"name": name, "job": job, "installed": bool(installed), "calls": n, "note": note, "verdict": verdict})

    add("rtk", "compresses shell output", S["rtk_installed"], (rtk or {}).get("total_commands", 0) if S["rtk_hook"] else 0,
        "all time" if S["rtk_hook"] else "hook missing: run rtk init -g")
    add("code-review-graph", "callers, blast radius (MCP)", has(S["mcp"], "code-review-graph"), calls(A["mcp"], "code-review-graph"))
    add("token-savior", "symbol-level reads (MCP)", has(S["mcp"], "token-savior"), calls(A["mcp"], "token-savior"))
    add("context-mode", "big-output sandbox (plugin)", has(S["plugins"], "context-mode"), calls(A["mcp"], "context-mode"))
    add("graphify", "project/docs map (skill)", S["graphify"], calls(A["skill"], "graphify"))
    hv = "missing" if not H["installed"] else ("active" if H["mode"] != "index" else "active") if (H["hooks"] and H["mode"] != "off") else "hookoff"
    add("token-history", "auto-recall of past chats (hooks)", H["installed"], H["inject"],
        f"{H['msgs']} messages indexed; ~{H['inject_tokens']:,} tokens injected in total" if H["hooks"] else "hooks not installed: token-history hooks install",
        verdict=hv)
    add("caveman", "terse replies (plugin)", has(S["plugins"], "caveman"), None, "active via session hooks",
        verdict="active" if has(S["plugins"], "caveman") else "missing")
    for a in ("lean-main", "lean-explorer", "lean-reviewer", "lean-coder"):
        if a == "lean-main":
            ok = a in S["agents"]
            add(a, "default agent", ok, None, "set as default" if S["default_agent"] == a else "installed, not the default",
                verdict=("active" if S["default_agent"] == a else "installed") if ok else "missing")
        else:
            add(a, "subagent", a in S["agents"], calls(A["agent"], a))
    return rows


def team_rows(T, start, rank="fresh"):
    rows = []
    for dv in T["devices"]:
        c = {"req": 0, "in": 0, "cw": 0, "cr": 0, "out": 0}
        for x in dv["days"]:
            if not start or x["d"] >= start:
                for k in c:
                    c[k] += x.get(k, 0)
        c["fresh"] = c["cw"] + c["out"] + c["in"]
        age = None
        if not dv["local"]:
            try:
                age = (time.time() - datetime.fromisoformat(dv["exported"]).timestamp()) / 3600
            except (ValueError, TypeError):
                age = float("inf")
        rows.append(dict(c, device=dv["device"], local=dv["local"], age_hours=age, stale=bool(age is not None and age > 36)))
    key = rank if rank in RANK_METRICS else "fresh"
    rows.sort(key=lambda r: -r[key])
    total = sum(r[key] for r in rows) or 1
    for r in rows:
        r["share"] = r[key] / total
    return rows


def session_rows(data, start, project=None, limit=10):
    out = []
    for s in data["sessions"]:
        end = _local_date(s["end"])
        if (start and end < start) or (project and project != "all" and s["p"] != project):
            continue
        avg = (s["cr"] + s["cw"] + s.get("in", 0)) / s["req"] if s["req"] else 0
        out.append({"end": end, "project": s["p"], "sub": s["sub"], "req": s["req"], "avg": avg, "cr": s["cr"],
                    "cw": s["cw"], "out": s["out"], "large": avg >= LARGE_CONTEXT})
    out.sort(key=lambda r: -r["cr"])
    return out[:limit]


def view(data, rng="30", project="all", rank="fresh", today=None):
    """Everything one screen needs for a given range/project, computed once so every UI agrees."""
    rng = rng if rng in RANGES else "30"
    start = range_start(rng, today)
    A = aggregate(data, start, project)
    inp = A["cr"] + A["cw"] + A["in"]
    tool_tokens = sum(v[1] for v in A["tool"].values()) / 4
    tools = sorted(({"name": n, "calls": c, "tokens": ch / 4} for n, (c, ch) in A["tool"].items()), key=lambda r: -r["tokens"])[:8]
    cmds = sorted(({"name": n, "n": v} for n, v in A["bash"].items()), key=lambda r: -r["n"])[:8]
    T = data["team"]
    return {
        "generated": data["generated"], "range": rng, "range_label": RANGE_LABELS[rng], "start": start, "project": project,
        "projects": sorted({b["p"] for b in data["buckets"]}), "device": T["device"],
        "totals": {"req": A["req"], "in": A["in"], "cw": A["cw"], "cr": A["cr"], "out": A["out"], "tool_tokens": tool_tokens,
                   "reread_pct": round(A["cr"] / inp * 100) if inp else None},
        "rtk": data["rtk"], "days": day_series(A, start, today), "tools": tools, "commands": cmds,
        "stack": stack_rows(data["status"], data["rtk"], A),
        "team": {"dir": T["dir"], "rank": rank if rank in RANK_METRICS else "fresh", "rows": team_rows(T, start, rank)},
        "sessions": session_rows(data, start, project),
    }


# --------------------------------------------------------------------------- demo data
def demo_payload():
    """Deterministic synthetic data for `--demo` (previews and screenshots). No real usage, names or paths."""
    import random
    rng = random.Random(7)
    today = date.today()
    buckets = []
    projects = {"web-app": 1.0, "api-server": 0.7, "mobile-client": 0.45}
    for back in range(44, -1, -1):
        day = today - timedelta(days=back)
        weekend = day.weekday() >= 5
        for proj, weight in projects.items():
            if rng.random() > (0.35 if weekend else 0.85) * (0.6 + weight * 0.4):
                continue
            req = max(3, int(rng.gauss(52, 22) * weight * (0.4 if weekend else 1)))
            ctx = rng.randint(45_000, 190_000)
            cw = int(req * rng.randint(1_800, 3_400))
            buckets.append({
                "d": day.isoformat(), "p": proj, "req": req, "in": req * 3, "cw": cw, "cr": int(req * ctx), "out": int(req * rng.randint(320, 700)),
                "tool": {"Bash": [req // 3, req * 420], "Read": [req // 5, req * 900], "Grep": [req // 6, req * 260], "Edit": [req // 8, req * 40],
                         "WebFetch": [req // 25, req * 300], "mcp__code-review-graph__query_graph_tool": [req // 14, req * 90]},
                "bash": {"git": req // 6, "npm": req // 9, "pytest": req // 12, "ls": req // 10, "docker": req // 30, "rg": req // 14},
                "mcp": {"code-review-graph": req // 14, "plugin_context-mode_context-mode": req // 20},
                "agent": {"lean-explorer": req // 25}, "skill": {}})
    sessions = []
    for i in range(14):
        req, avg = rng.randint(8, 210), rng.randint(30_000, 240_000)
        end = today - timedelta(days=rng.randint(0, 25))
        sessions.append({"id": f"demo{i:04d}", "p": rng.choice(list(projects)), "start": end.isoformat() + "T09:00:00+00:00", "end": end.isoformat() + "T12:30:00+00:00",
                         "req": req, "in": req * 3, "cr": int(req * avg * 0.96), "cw": int(req * avg * 0.04), "out": req * 520, "sub": False})
    day_rows = lambda k, mult: [{"d": (today - timedelta(days=b)).isoformat(), "req": int(40 * mult), "in": 100, "cw": int(90_000 * mult), "cr": int(5_200_000 * mult), "out": int(24_000 * mult)}
                                for b in range(30, -1, -1) if (b + k) % 7 not in (5, 6)]
    now = datetime.now().astimezone()
    devices = [{"device": "alice-laptop", "local": True, "exported": "", "days": [], "mult": 1.0},
               {"device": "bob-desktop", "local": False, "exported": (now - timedelta(minutes=9)).isoformat(timespec="seconds"), "days": day_rows(2, 0.8)},
               {"device": "carol-macbook", "local": False, "exported": (now - timedelta(days=4)).isoformat(timespec="seconds"), "days": day_rows(4, 0.35)}]
    data = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "buckets": buckets, "sessions": sessions}
    devices[0]["days"] = local_days(data)
    for d in devices:
        d.pop("mult", None)
    data["status"] = {"rtk_installed": True, "rtk_hook": True, "mcp": ["code-review-graph", "token-savior"], "plugins": ["caveman@caveman", "context-mode@context-mode"],
                      "agents": ["lean-coder", "lean-explorer", "lean-main", "lean-reviewer"], "default_agent": "lean-main", "graphify": True,
                      "history": {"installed": True, "hooks": True, "mode": "recall", "msgs": 1840, "inject": 23, "inject_tokens": 4100}}
    data["rtk"] = {"total_commands": 1240, "total_saved": 412_000, "avg_savings_pct": 61.5}
    data["team"] = {"dir": "~/Sync/token-usage", "device": "alice-laptop", "devices": devices}
    return data
