import json
import os
import subprocess
import sys
import unittest

from helpers import BIN, USAGE, TempHome, load_script, row, write_jsonl


class StatusLine(TempHome):
    def setUp(self):
        super().setUp()
        self.sl = load_script("token-statusline")

    def test_context_is_the_last_request_input_size(self):
        p = self.transcript()
        write_jsonl(p, [row("assistant", "a1", [{"type": "text", "text": "x"}], usage=USAGE(i=1, cw=100, cr=900), request_id="r1"),
                        row("user", "u1", "hello there this is a long enough user message"),
                        row("assistant", "a2", [{"type": "text", "text": "y"}], usage=USAGE(i=2, cw=300, cr=4700), request_id="r2")])
        self.assertEqual(self.sl.current_context(p), 5002)

    def test_missing_or_junk_transcripts_are_handled(self):
        self.assertIsNone(self.sl.current_context(os.path.join(self.home, "nope.jsonl")))
        p = self.transcript()
        os.makedirs(os.path.dirname(p))
        with open(p, "w") as f:
            f.write("garbage\n{\"usage\": broken\n")
        self.assertIsNone(self.sl.current_context(p))


class CommandLine(TempHome):
    """Runs the real scripts as subprocesses against a synthetic HOME."""

    def run_script(self, *args):
        env = dict(os.environ)
        return subprocess.run([sys.executable, os.path.join(BIN, args[0]), *args[1:]], capture_output=True, text=True, env=env, timeout=60)

    def setUp(self):
        super().setUp()
        write_jsonl(self.transcript(), [row("assistant", "a1", [{"type": "text", "text": "x"}], usage=USAGE(i=1, cw=50, cr=950, out=10), request_id="r1")] * 3)

    def test_report_counts_the_request_once(self):
        r = self.run_script("token-report", "--days", "3")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("requests: 1", r.stdout)

    def test_export_then_team_table(self):
        share = os.path.join(self.home, "share")
        r = self.run_script("token-report", "--export", share, "--name", "dev-one")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(share, "dev-one.json")))
        self.assertIn("no prompts, commands, file or project names", r.stdout)
        r = self.run_script("token-report", "--team", share, "--name", "dev-two")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("dev-one", r.stdout); self.assertIn("dev-two (this device)", r.stdout)


class DashboardServer(TempHome):
    def test_payload_shape_and_page_security_headers(self):
        import threading, urllib.request, urllib.error
        dash = load_script("token-dashboard")
        write_jsonl(self.transcript(), [row("assistant", "a1", [{"type": "text", "text": "x"}], usage=USAGE(cw=5, cr=95), request_id="r1")])
        srv = dash.ThreadingHTTPServer(("127.0.0.1", 0), dash.Handler)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with urllib.request.urlopen(base + "/api/data") as resp:
                d = json.load(resp)
            self.assertEqual(d["buckets"][0]["cr"], 95)
            self.assertIn("team", d); self.assertIn("status", d)
            with urllib.request.urlopen(base + "/") as page:
                self.assertIn("Content-Security-Policy", page.read().decode())
            for path, host, code in (("/api/data", "evil.example.com", 421), ("/nope", "127.0.0.1", 404)):
                req = urllib.request.Request(base + path, headers={"Host": host})
                with self.assertRaises(urllib.error.HTTPError) as cm:
                    urllib.request.urlopen(req)
                self.assertEqual(cm.exception.code, code)
                cm.exception.close()
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
