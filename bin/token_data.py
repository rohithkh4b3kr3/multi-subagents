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
import time
from datetime import datetime, timezone

ROOT = os.path.expanduser("~/.claude/projects")
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
    for line in open(path, encoding="utf-8", errors="ignore"):
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
             "req": 0, "cr": 0, "cw": 0, "out": 0, "sub": len(rel) > 2}
        for rid, (ts, i, cw, cr, out) in p["reqs"].items():
            if rid in seen_req:
                continue
            seen_req.add(rid)
            b = bucket(ts, proj)
            b["req"] += 1; b["in"] += i; b["cw"] += cw; b["cr"] += cr; b["out"] += out
            s["req"] += 1; s["cw"] += cw; s["cr"] += cr; s["out"] += out
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
