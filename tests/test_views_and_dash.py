import json
import os
import re
import subprocess
import sys
import unittest

from helpers import BIN, USAGE, TempHome, load_script, now_ts, row, write_jsonl
import token_data as td


def luminance(hexcol):
    def ch(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = (int(hexcol[i:i + 2], 16) for i in (1, 3, 5))
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def contrast(a, b):
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


class Themes(unittest.TestCase):
    def test_every_theme_is_complete_and_readable(self):
        keys = {"label", "dark", "bg", "surface", "border", "fg", "dim", "accent", "accent2", "good", "warn", "bad", "cyan"}
        for name, t in td.THEMES.items():
            self.assertEqual(set(t), keys, name)
            for bgk in ("bg", "surface"):
                self.assertGreaterEqual(contrast(t["fg"], t[bgk]), 6, f"{name} fg on {bgk}")
                self.assertGreaterEqual(contrast(t["dim"], t[bgk]), 4.5, f"{name} dim on {bgk}")
                for k in ("accent", "accent2", "good", "warn", "bad", "cyan"):
                    self.assertGreaterEqual(contrast(t[k], t[bgk]), 3.0, f"{name} {k} on {bgk}")
            self.assertGreaterEqual(contrast(t["border"], t["bg"]), 1.3, f"{name} border must be visible")

    def test_css_covers_all_themes(self):
        css = td.theme_css()
        for name in td.THEMES:
            self.assertIn(f'data-theme="{name}"', css)

    def test_mix_endpoints(self):
        self.assertEqual(td.mix("#000000", "#ffffff", 0), "#000000")
        self.assertEqual(td.mix("#000000", "#ffffff", 1), "#ffffff")


class Formatting(unittest.TestCase):
    def test_compact_never_drops_zeros_from_whole_numbers(self):
        cases = {0: "0", 7: "7", 999: "999", 1000: "1K", 1500: "1.5K", 9990: "9.99K", 99999: "100K", 220453: "220K", 500000: "500K",
                 999999: "1M", 1234567: "1.23M", 16600000: "16.6M", 100000000: "100M", 2500000000: "2.5B"}
        for n, want in cases.items():
            self.assertEqual(td.compact(n), want, n)


class Fixture(TempHome):
    def build(self):
        def use(tid, name, inp, rid):
            return [row("assistant", "a" + tid, [{"type": "tool_use", "id": tid, "name": name, "input": inp}], usage=USAGE(cw=100, cr=9000, out=50), request_id=rid),
                    row("user", "r" + tid, [{"type": "tool_result", "tool_use_id": tid, "content": "x" * 80}])]
        rows = use("t1", "Agent", {"subagent_type": "lean-explorer"}, "r1") + use("t2", "Bash", {"command": "ls"}, "r2")
        rows.append(row("assistant", "old", [{"type": "text", "text": "x"}], ts="2020-01-05T10:00:00.000Z", usage=USAGE(cr=777), request_id="r-old"))
        write_jsonl(self.transcript(), rows)
        agents = os.path.join(self.home, ".claude", "agents"); os.makedirs(agents, exist_ok=True)
        for n in ("lean-explorer", "lean-coder", "lean-main"):
            open(os.path.join(agents, n + ".md"), "w").close()
        with open(os.path.join(self.home, ".claude", "settings.json"), "w") as f:
            json.dump({"agent": "lean-main"}, f)
        return td.payload()


class Views(Fixture):
    def test_range_filters_and_totals(self):
        data = self.build()
        today = td.view(data, "today")
        self.assertEqual(today["totals"]["req"], 2)
        self.assertEqual(today["totals"]["cr"], 18000)               # the 2020 request is outside the range
        allv = td.view(data, "all")
        self.assertEqual(allv["totals"]["req"], 3)
        self.assertEqual(allv["totals"]["cr"], 18777)
        self.assertGreater(len(allv["days"]), 365)                   # zero-filled from 2020 to today
        self.assertEqual(allv["days"][0]["d"], "2020-01-05")

    def test_reread_percentage_and_tool_estimates(self):
        v = td.view(self.build(), "today")
        self.assertEqual(v["totals"]["reread_pct"], round(18000 / (18000 + 200 + 2) * 100))
        self.assertEqual(v["totals"]["tool_tokens"], 160 / 4)
        self.assertEqual({t["name"] for t in v["tools"]}, {"Agent", "Bash"})

    def test_stack_verdicts(self):
        rows = {r["name"]: r for r in td.view(self.build(), "today")["stack"]}
        self.assertEqual(rows["lean-explorer"]["verdict"], "used")
        self.assertEqual(rows["lean-coder"]["verdict"], "unused")
        self.assertEqual(rows["lean-reviewer"]["verdict"], "missing")
        self.assertEqual(rows["lean-main"]["verdict"], "active")
        self.assertEqual(rows["rtk"]["verdict"], "missing")
        self.assertEqual(rows["token-history"]["verdict"], "missing")

    def test_team_ranking_and_stale_flag(self):
        data = self.build()
        old = "2020-01-01T00:00:00+00:00"
        data["team"]["devices"].append({"device": "alice", "local": False, "exported": old,
                                        "days": [{"d": td.range_start("today"), "req": 1, "in": 0, "cw": 10 ** 6, "cr": 5, "out": 0}]})
        rows = td.view(data, "today", rank="fresh")["team"]["rows"]
        self.assertEqual([r["device"] for r in rows][0], "alice")
        self.assertTrue(rows[0]["stale"] and not rows[1]["stale"])
        self.assertAlmostEqual(sum(r["share"] for r in rows), 1.0)
        by_req = td.view(data, "today", rank="req")["team"]["rows"]
        self.assertEqual(by_req[0]["device"], data["team"]["device"])

    def test_sessions_flag_large_contexts(self):
        write_jsonl(self.transcript("big.jsonl"), [row("assistant", "b1", [{"type": "text", "text": "x"}], usage=USAGE(cw=10, cr=250_000), request_id="rb")])
        rows = td.view(td.payload(), "today")["sessions"]
        self.assertTrue(rows[0]["large"] and rows[0]["avg"] >= td.LARGE_CONTEXT)


class DashRender(Fixture):
    def setUp(self):
        super().setUp()
        self.dash = load_script("dash")
        self.data = self.build()
        self.data["team"]["devices"].append({"device": "alice-pc", "local": False, "exported": "2026-01-01T00:00:00+00:00",
                                             "days": [{"d": td.range_start("30"), "req": 4, "in": 0, "cw": 5000, "cr": 90000, "out": 800}]})

    def frame(self, tab, width, **kw):
        P = self.dash.Painter(**kw.pop("painter", {}))
        st = self.dash.State(P.name, "30", tab)
        st.data, st.fetched = self.data, 1.0
        return P, self.dash.render(P, st, width, None)

    def test_every_line_fits_the_width_exactly(self):
        for tab in self.dash.TABS:
            for width in (50, 64, 80, 100, 140, 200):
                for theme in ("tokyo-night", "github-light"):
                    P, lines = self.frame(tab, width, painter={"theme": theme})
                    for ln in lines:
                        self.assertEqual(self.dash.vlen(ln), width, f"{tab} @ {width} {theme}: {self.dash.ANSI.sub('', ln)!r}")

    def test_no_colour_output_has_no_escape_codes(self):
        _, lines = self.frame("overview", 100, painter={"color": False})
        self.assertFalse(any("\x1b" in l for l in lines))

    def test_ascii_mode_is_pure_ascii(self):
        self.dash.ELLIPSIS = "~"
        try:
            for tab in self.dash.TABS:
                _, lines = self.frame(tab, 90, painter={"unicode_ok": False, "color": False})
                self.assertTrue(all(ord(c) < 128 for l in lines for c in l), tab)
        finally:
            self.dash.ELLIPSIS = "…"

    def test_screens_show_the_expected_content(self):
        expect = {"overview": ("Where your tokens go", "Requests", "per day"), "stack": ("Is the stack actually used?", "lean-explorer", "Used"),
                  "team": ("Who is using how much", "alice-pc", "(stale)"), "sessions": ("Biggest sessions", "Avg ctx")}
        for tab, needles in expect.items():
            _, lines = self.frame(tab, 120, painter={"color": False})
            blob = "\n".join(lines)
            for n in needles:
                self.assertIn(n, blob, f"{tab}: {n}")

    def test_column_chart_and_bars_handle_edge_cases(self):
        P = self.dash.Painter(color=False)
        self.assertTrue(self.dash.column_chart(P, [], 60, 6))
        flat = self.dash.column_chart(P, [("2026-01-01", 0), ("2026-01-02", 0)], 60, 6)
        self.assertEqual(len(flat), 7)
        many = self.dash.column_chart(P, [(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", i) for i in range(400)], 60, 6)
        self.assertTrue(all(self.dash.vlen(l) <= 60 for l in many))

    def test_clip_keeps_escape_codes_balanced_and_width_exact(self):
        P = self.dash.Painter()
        s = P.c("accent", "hello wide world", True) + " tail"
        for w in (1, 5, 11, 40):
            self.assertLessEqual(self.dash.vlen(self.dash.clip(s, w)), w)
        self.assertEqual(self.dash.vlen(self.dash.clip("日本語日本語", 7)), 7)   # wide characters count as two cells


class Layout(unittest.TestCase):
    def setUp(self):
        self.dash = load_script("dash", alias="dash_layout")

    def test_columns_drop_lowest_priority_first_and_always_fit(self):
        cols = [(None, 14, 99), (20, 20, 60), (9, 9, 99), (8, 8, 50), (8, 8, 40), (8, 8, 55), (15, 15, 95)]
        for inner in (30, 45, 60, 80, 106, 160):
            kept = self.dash.layout(cols, inner)
            self.assertLessEqual(sum(w for _, w in kept) + 2 * (len(kept) - 1), max(inner, 14 + 9 + 2), inner)
            names = [i for i, _ in kept]
            self.assertIn(0, names); self.assertIn(2, names)                    # the essential columns always survive
        wide = [i for i, _ in self.dash.layout(cols, 160)]
        self.assertEqual(wide, list(range(7)))                                  # nothing dropped when there is room
        tight = [i for i, _ in self.dash.layout(cols, 60)]
        self.assertNotIn(4, tight)                                              # lowest priority (Output) goes first

    def test_stretch_column_uses_the_leftover_space(self):
        kept = dict(self.dash.layout([(None, 10, 99), (10, 10, 99)], 50))
        self.assertEqual(kept[0], 38)


class Demo(unittest.TestCase):
    def test_demo_data_is_synthetic_complete_and_deterministic(self):
        a, b = td.demo_payload(), td.demo_payload()
        self.assertEqual(a["buckets"], b["buckets"])
        blob = json.dumps(a)
        import getpass
        for real in ("/home/", "C:\\\\Users", getpass.getuser(), os.path.basename(os.path.expanduser("~"))):
            self.assertNotIn(real, blob)
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[a-z]{2,}", blob), "no email addresses in demo data")
        v = td.view(a, "30")
        self.assertGreater(v["totals"]["req"], 100)
        self.assertEqual(len(v["team"]["rows"]), 3)
        self.assertTrue(any(r["stale"] for r in v["team"]["rows"]))
        self.assertEqual({r["verdict"] for r in v["stack"]} - {"used", "unused", "active", "missing", "installed", "hookoff"}, set())

    def test_demo_mode_never_reads_real_data(self):
        r = subprocess.run([sys.executable, os.path.join(BIN, "dash"), "--demo", "--once", "--tab", "team", "--width", "100"],
                           capture_output=True, text=True, env=dict(os.environ, HOME="/nonexistent", USERPROFILE="/nonexistent"), timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("alice-laptop", r.stdout); self.assertIn("carol-macbook", r.stdout)


class DashCommandLine(TempHome):
    def run_dash(self, *args, stdin=None):
        return subprocess.run([sys.executable, os.path.join(BIN, "dash"), *args], capture_output=True, text=True,
                              input=stdin, env=dict(os.environ), timeout=60)

    def test_once_prints_a_frame_and_exits(self):
        write_jsonl(self.transcript(), [row("assistant", "a", [{"type": "text", "text": "x"}], usage=USAGE(cw=1, cr=9), request_id="r")])
        r = self.run_dash("--once", "--tab", "all", "--width", "100")
        self.assertEqual(r.returncode, 0, r.stderr)
        for needle in ("token stack", "Where your tokens go", "Is the stack actually used?", "Biggest sessions"):
            self.assertIn(needle, r.stdout)

    def test_bad_theme_or_range_is_rejected(self):
        for args in (("--once", "--theme", "nope"), ("--once", "--range", "yesterday")):
            r = self.run_dash(*args)
            self.assertEqual(r.returncode, 1)
            self.assertIn("choose from", r.stderr)

    @unittest.skipUnless(os.path.exists("/bin/dash") or os.path.exists("/usr/bin/dash"), "system dash shell not present")
    def test_shell_invocations_pass_through_to_the_real_dash(self):
        self.assertEqual(self.run_dash("-c", "echo hi-from-shell").stdout.strip(), "hi-from-shell")
        self.assertEqual(self.run_dash(stdin="echo piped-script\n").stdout.strip(), "piped-script")


@unittest.skipIf(os.name == "nt", "pty is POSIX-only")
class DashInteractive(TempHome):
    def test_keys_resize_and_clean_exit_in_a_real_terminal(self):
        import fcntl, pty, select, signal, struct, termios, time

        def drain(fd, t):
            out, end = b"", time.time() + t
            while time.time() < end:
                if select.select([fd], [], [], 0.1)[0]:
                    try:
                        d = os.read(fd, 65536)
                    except OSError:
                        break
                    if not d:
                        break
                    out += d
            return out.decode("utf-8", "ignore")

        strip = lambda s: re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", s)
        pid, fd = pty.fork()
        if pid == 0:
            os.environ.update(TERM="xterm-256color", COLORTERM="truecolor")
            os.execv(sys.executable, [sys.executable, os.path.join(BIN, "dash")])
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 36, 112, 0, 0)); os.kill(pid, signal.SIGWINCH)
            first = drain(fd, 2.5)
            self.assertIn("\x1b[?1049h", first); self.assertIn("\x1b[?25l", first)
            self.assertIn("Where your tokens go", strip(first))
            for key, needle in ((b"\x1b[C", "Is the stack actually used?"), (b"\x1b[C", "Who is using how much"), (b"\t", "Biggest sessions"),
                                (b"\x1b[Z", "Who is using how much"), (b"?", "Themes:")):
                os.write(fd, key)
                self.assertIn(needle, strip(drain(fd, 0.6)), key)
            os.write(fd, b"\x1b"); drain(fd, 0.4)
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 30, 70, 0, 0)); os.kill(pid, signal.SIGWINCH)
            self.assertTrue(drain(fd, 1.0))
            os.write(fd, b"q")
            tail = drain(fd, 1.0)
            _, status = os.waitpid(pid, 0)
            self.assertEqual(os.waitstatus_to_exitcode(status), 0)
            self.assertIn("\x1b[?1049l", tail); self.assertIn("\x1b[?25h", tail)
        finally:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


if __name__ == "__main__":
    unittest.main()
