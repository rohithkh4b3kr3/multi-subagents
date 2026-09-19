import json
import os
import unittest

from helpers import USAGE, TempHome, row, write_jsonl
import token_data


class UsageCounting(TempHome):
    def test_usage_counted_once_per_request(self):
        # Claude Code writes one response as several rows repeating the same usage.
        u = USAGE(i=2, cw=1000, cr=5000, out=40)
        write_jsonl(self.transcript(), [row("assistant", f"u{i}", [{"type": "text", "text": "x"}], usage=u, request_id="req-1") for i in range(3)])
        b = token_data.scan(self.projects)["buckets"]
        self.assertEqual(sum(x["req"] for x in b), 1)
        self.assertEqual(sum(x["cr"] for x in b), 5000)
        self.assertEqual(sum(x["cw"] for x in b), 1000)
        self.assertEqual(sum(x["out"] for x in b), 40)

    def test_same_request_in_two_files_is_counted_once(self):
        u = USAGE(cr=700)
        for name in ("a.jsonl", "b.jsonl"):        # a resumed session can copy earlier history
            write_jsonl(self.transcript(name), [row("assistant", "u1", [{"type": "text", "text": "x"}], usage=u, request_id="req-shared")])
        self.assertEqual(sum(x["cr"] for x in token_data.scan(self.projects)["buckets"]), 700)

    def test_distinct_requests_add_up(self):
        write_jsonl(self.transcript(), [row("assistant", f"u{i}", [{"type": "text", "text": "x"}], usage=USAGE(cr=100), request_id=f"r{i}") for i in range(4)])
        d = token_data.scan(self.projects)
        self.assertEqual(sum(x["cr"] for x in d["buckets"]), 400)
        self.assertEqual(d["sessions"][0]["req"], 4)


class ToolAccounting(TempHome):
    def test_tools_commands_mcp_agents_skills(self):
        def use(tid, name, inp, result):
            return [row("assistant", "a" + tid, [{"type": "tool_use", "id": tid, "name": name, "input": inp}], usage=USAGE(), request_id="q" + tid),
                    row("user", "r" + tid, [{"type": "tool_result", "tool_use_id": tid, "content": result}])]
        rows = []
        rows += use("t1", "Bash", {"command": "cd /x && ls -la"}, "y" * 400)
        rows += use("t2", "Bash", {"command": "for f in a b; do echo $f; done"}, "z" * 40)
        rows += use("t3", "mcp__plugin_context-mode_context-mode__ctx_execute", {}, "ok")
        rows += use("t4", "Agent", {"subagent_type": "lean-explorer"}, "done")
        rows += use("t5", "Skill", {"skill": "graphify"}, "ok")
        write_jsonl(self.transcript(), rows)
        b = token_data.scan(self.projects)["buckets"][0]
        self.assertEqual(b["tool"]["Bash"], [2, 440])
        self.assertEqual(b["bash"].get("ls"), 1)                  # 'cd' is skipped, the real command is named
        self.assertNotIn("cd", b["bash"])
        self.assertEqual(b["bash"].get("(shell)"), 1)             # loops fall back to a neutral label
        self.assertEqual(b["mcp"], {"plugin_context-mode_context-mode": 1})
        self.assertEqual(b["agent"], {"lean-explorer": 1})
        self.assertEqual(b["skill"], {"graphify": 1})

    def test_command_arguments_are_never_recorded(self):
        write_jsonl(self.transcript(), [row("assistant", "a", [{"type": "tool_use", "id": "t", "name": "Bash",
                                                              "input": {"command": "curl -H 'Authorization: SECRETVALUE' https://x.example"}}], usage=USAGE())])
        self.assertNotIn("SECRETVALUE", json.dumps(token_data.scan(self.projects)))


class TeamSharing(TempHome):
    DATA = {"buckets": [{"d": "2026-01-01", "p": "secretproj", "req": 1, "in": 1, "cw": 2, "cr": 3, "out": 4,
                         "tool": {"Bash": [1, 999]}, "bash": {"ls": 5}, "mcp": {}, "agent": {}, "skill": {}}]}

    def test_export_contains_numbers_only_by_default(self):
        path, days = token_data.export(os.path.join(self.home, "share"), "Alice PC", False, self.DATA)
        with open(path, encoding="utf-8") as f:
            text = f.read()
        self.assertEqual(days, 1)
        for leaked in ("secretproj", '"ls"', "Bash", "999"):
            self.assertNotIn(leaked, text)
        self.assertEqual(json.loads(text)["device"], "Alice PC")

    def test_projects_are_included_only_on_request(self):
        path, _ = token_data.export(os.path.join(self.home, "share"), "bob", True, self.DATA)
        with open(path, encoding="utf-8") as f:
            self.assertIn("secretproj", f.read())

    def test_roundtrip_and_skip_local_device(self):
        d = os.path.join(self.home, "share")
        token_data.export(d, "alice-pc", False, self.DATA)
        team = token_data.read_team(d, skip_device="ALICE-PC")     # case-insensitive
        self.assertEqual(team, [])
        team = token_data.read_team(d, skip_device="someone-else")
        self.assertEqual(team[0]["device"], "alice-pc")
        self.assertEqual(team[0]["days"][0]["cr"], 3)

    def test_hostile_files_are_neutralised(self):
        d = os.path.join(self.home, "share"); os.makedirs(d)
        def put(name, content):
            with open(os.path.join(d, name), "w") as f:
                f.write(content if isinstance(content, str) else json.dumps(content))
        put("evil.json", {"schema": 1, "device": "<img src=x onerror=alert(1)>", "exported": "2026-09-10T08:00:00",
                          "buckets": [{"d": "2026-09-10", "req": "999", "in": -5, "cw": 1e30, "cr": 7, "out": True},
                                      {"d": "not-a-date", "req": 5}, "garbage", None]})
        put("future.json", {"schema": 2, "device": "future", "buckets": []})
        put("broken.json", "{not json")
        team = token_data.read_team(d)
        self.assertEqual([t["device"] for t in team], ["_img src_x onerror_alert_1__"])
        day = team[0]["days"][0]
        self.assertEqual((day["req"], day["in"], day["cw"], day["cr"], day["out"]), (0, 0, 0, 7, 0))
        self.assertEqual(len(team[0]["days"]), 1)                  # the bad date was dropped

    def test_local_days_merges_projects(self):
        data = {"buckets": [{"d": "2026-01-01", "req": 1, "in": 0, "cw": 1, "cr": 2, "out": 3},
                            {"d": "2026-01-01", "req": 2, "in": 0, "cw": 1, "cr": 2, "out": 3}]}
        self.assertEqual(token_data.local_days(data)[0]["req"], 3)


if __name__ == "__main__":
    unittest.main()
