import argparse
import contextlib
import io
import json
import os
import sys
import unittest

from helpers import USAGE, TempHome, load_script, row, write_jsonl


def text(t):
    return [{"type": "text", "text": t}]


class Base(TempHome):
    def setUp(self):
        super().setUp()
        self.th = load_script("token-history", alias=f"th_{id(self)}")     # env is applied at import time

    def add_chat(self, session, messages, project="proj", name=None):
        rows = []
        for i, (role, t) in enumerate(messages):
            rows.append(row(role, f"{session}-{i}", t if role == "user" else text(t), session=session,
                            cwd=f"/work/{project}", usage=USAGE() if role == "assistant" else None))
        write_jsonl(self.transcript(name or f"{session}.jsonl", project), rows)

    def run_hook(self, event, **kw):
        payload = {"hook_event_name": event, "cwd": "/work/proj", "session_id": "current", **kw}
        out = io.StringIO()
        with contextlib.redirect_stdout(out), unittest.mock.patch.object(sys, "stdin", io.StringIO(json.dumps(payload))):
            self.th.cmd_hook(None)
        return out.getvalue()


import unittest.mock  # noqa: E402  (used by run_hook)


class Redaction(Base):
    PAD = " The deployment reads several environment files at startup, then connects to the database and warms its caches before it accepts traffic from the load balancer in production use."

    def test_credential_messages_are_dropped(self):
        for t in ("my password is hunter2 please use it", "1234 , that is it", "I set my pin to 4821 for the bank app so remind me later"):
            self.assertIsNone(self.th.clean(t), t)

    def test_common_secret_formats_are_redacted(self):
        fakes = ["sk-" + "a1" * 20, "ghp_" + "Ab3" * 12, "AKIA" + "ABCDEFGH12345678",
                 "eyJ" + "a" * 12 + "." + "b" * 12 + "." + "c" * 12, "postgres://user:pw123@db.local:5432/app",
                 "AB12-CD34", "Bearer " + "z9" * 15]
        for f in fakes:
            out = self.th.clean(f"Config that we used for the run: {f} and the rest is fine." + self.PAD)
            self.assertIsNotNone(out, f)
            self.assertNotIn(f.split("://")[-1].split("@")[0] if "://" in f else f, out)
            self.assertIn("[redacted]", out)

    def test_normal_technical_text_is_kept(self):
        t = "The dashboard falls back to the next free port up to 8785 if 8765 is busy, and closes with the window."
        self.assertEqual(self.th.clean(t), t)

    def test_injected_recall_notes_are_never_reindexed(self):
        self.assertIsNone(self.th.clean("Auto-recalled from earlier sessions (local history search) - something" + self.PAD))


class Indexing(Base):
    def test_only_chat_text_is_indexed(self):
        rows = [
            row("user", "u1", "How do I fix the docker socket permission problem on my machine?"),
            row("assistant", "a1", text("Add your user to the docker group and log out and back in so the socket permission applies.")),
            row("assistant", "a2", [{"type": "tool_use", "id": "t", "name": "Bash", "input": {"command": "cat /etc/secret-config-file"}}]),
            row("user", "u2", [{"type": "tool_result", "tool_use_id": "t", "content": "TOOL-OUTPUT-MUST-NOT-BE-INDEXED " * 5}]),
            row("user", "u3", "<system-reminder>REMINDER-MUST-NOT-BE-INDEXED and more words here to be long enough</system-reminder>"),
            row("assistant", "a3", [{"type": "thinking", "thinking": "THINKING-MUST-NOT-BE-INDEXED " * 4}]),
            row("assistant", "a4", text("SIDECHAIN-MUST-NOT-BE-INDEXED because this comes from a subagent run of some kind."), isSidechain=True),
            row("user", "u4", "META-MUST-NOT-BE-INDEXED because this is an injected meta message from the harness", isMeta=True),
        ]
        write_jsonl(self.transcript(), rows)
        self.assertEqual(self.th.do_index(), 2)
        import sqlite3
        blob = " ".join(r[0] for r in sqlite3.connect(self.th.DB).execute("SELECT text FROM fts"))
        for banned in ("TOOL-OUTPUT", "REMINDER", "THINKING", "SIDECHAIN", "META", "secret-config-file"):
            self.assertNotIn(banned, blob)

    def test_incremental_and_idempotent(self):
        p = self.transcript()
        write_jsonl(p, [row("user", "u1", "Please explain how the incremental indexer keeps its offset per file.")])
        self.assertEqual(self.th.do_index(), 1)
        self.assertEqual(self.th.do_index(), 0)
        write_jsonl(p, [row("assistant", "a1", text("It stores the byte offset of the last complete line and continues from there next time."))])
        self.assertEqual(self.th.do_index(), 1)

    def test_subagent_transcripts_are_skipped(self):
        write_jsonl(os.path.join(self.projects, "proj", "s1", "subagents", "agent-x.jsonl"),
                    [row("assistant", "a1", text("This text lives in a subagent transcript and should be skipped entirely by the indexer."))])
        self.assertEqual(self.th.do_index(), 0)


class Seeded(Base):
    """A small synthetic history: two real topics plus gardening noise so word rarity behaves realistically."""

    def setUp(self):
        super().setUp()
        self.seed()

    def seed(self):
        self.add_chat("old1", [("user", "docker ps says permission denied on the docker socket, how do I fix the group membership?"),
                               ("assistant", "Run newgrp docker or log out and back in so the docker group membership applies to the socket.")])
        self.add_chat("old2", [("user", "The editor keeps getting killed with signal nine and the fan spins, is memory the problem?"),
                               ("assistant", "The kernel out-of-memory killer ended the editor because swap was small and memory ran out.")], project="other")
        for i in range(8):
            self.add_chat(f"noise{i}", [("user", f"Topic number {i}: please summarise chapter {i} about gardening tomatoes and watering schedules."),
                                        ("assistant", f"Chapter {i} covers soil, sunlight and a weekly watering schedule for tomato plants number {i}.")])
        self.th.do_index()


class Search(Seeded):
    def test_finds_the_relevant_note(self):
        res = self.th.do_search("docker socket permission denied", limit=3)
        self.assertTrue(res and "newgrp docker" in res[0]["text"] + (res[1]["text"] if len(res) > 1 else ""))

    def test_project_filter_and_session_exclusion(self):
        self.assertFalse(self.th.do_search("editor killed memory swap", project="proj"))
        self.assertTrue(self.th.do_search("editor killed memory swap", project="other"))
        self.assertFalse(self.th.do_search("docker socket permission denied", exclude_session="old1", project="proj", min_hits=3))

    def test_query_syntax_cannot_break_the_search(self):
        for q in ('"; DROP TABLE msgs; --', "docker AND OR NOT ( )", "* NEAR(a b)"):
            self.th.do_search(q)                       # must not raise


class AutoRecallHook(Seeded):
    RELEVANT = "docker ps gives permission denied on the docker socket again, how do I fix the group membership"

    def test_relevant_prompt_injects_small_labelled_notes(self):
        out = json.loads(self.run_hook("UserPromptSubmit", prompt=self.RELEVANT))
        ctx = out["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Auto-recalled from earlier sessions", ctx)
        self.assertIn("newgrp docker", ctx)
        self.assertLessEqual(len(ctx), 1500)
        self.assertIn("token-history: recalled", out["systemMessage"])

    def test_no_repeat_of_the_same_notes(self):
        self.assertTrue(self.run_hook("UserPromptSubmit", prompt=self.RELEVANT))
        self.assertEqual(self.run_hook("UserPromptSubmit", prompt=self.RELEVANT), "")

    def test_vague_unrelated_short_and_slash_prompts_inject_nothing(self):
        for p in ("please continue with the previous thing we discussed earlier today",
                  "what is the capital of france and how many people live there today",
                  "can you fix it and make it better and then tell me what happened next",
                  "fix it", "/clear"):
            self.assertEqual(self.run_hook("UserPromptSubmit", prompt=p), "", p)

    def test_other_project_is_not_searched_by_default(self):
        self.assertEqual(self.run_hook("UserPromptSubmit", prompt=self.RELEVANT, cwd="/work/unrelated"), "")

    def test_modes_and_cap(self):
        self.th.save_cfg(mode="off"); self.assertEqual(self.run_hook("UserPromptSubmit", prompt=self.RELEVANT), "")
        self.th.save_cfg(mode="index"); self.assertEqual(self.run_hook("UserPromptSubmit", prompt=self.RELEVANT), "")
        self.th.save_cfg(mode="recall", max_per_session=1)
        self.assertTrue(self.run_hook("UserPromptSubmit", prompt=self.RELEVANT))
        self.assertEqual(self.run_hook("UserPromptSubmit", prompt="the editor keeps getting killed with signal nine, is memory or swap the problem?", cwd="/work/other"), "")

    def test_the_hook_never_raises_and_never_prints_on_bad_input(self):
        for bad in ("not json {{{", "", "[]", "null"):
            out = io.StringIO()
            with contextlib.redirect_stdout(out), unittest.mock.patch.object(sys, "stdin", io.StringIO(bad)):
                self.th.cmd_hook(None)
            self.assertEqual(out.getvalue(), "")

    def test_injections_are_logged_for_cost_review(self):
        self.run_hook("UserPromptSubmit", prompt=self.RELEVANT)
        import sqlite3
        n, chars = sqlite3.connect(self.th.DB).execute("SELECT count(*), sum(chars) FROM events WHERE kind='inject'").fetchone()
        self.assertEqual(n, 1); self.assertGreater(chars, 0)


class HooksInstaller(Base):
    @staticmethod
    def jload(p):
        with open(p) as f:
            return json.load(f)

    def test_install_is_idempotent_and_preserves_other_hooks(self):
        sp = os.path.join(self.home, ".claude", "settings.json")
        with open(sp, "w") as f:
            json.dump({"theme": "dark", "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "rtk hook claude"}]}],
                                                  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": "other-tool hook"}]}]}}, f)
        for _ in range(2):
            with contextlib.redirect_stdout(io.StringIO()):
                self.th.cmd_hooks(argparse.Namespace(action="install"))
        d = self.jload(sp)
        cmds = lambda ev: [h["command"] for e in d["hooks"][ev] for h in e["hooks"]]
        self.assertEqual(sum("token-history" in c for c in cmds("UserPromptSubmit")), 1)
        self.assertEqual(sum("token-history" in c for c in cmds("SessionStart")), 1)
        self.assertIn("other-tool hook", cmds("UserPromptSubmit"))
        self.assertEqual(d["theme"], "dark")
        with contextlib.redirect_stdout(io.StringIO()):
            self.th.cmd_hooks(argparse.Namespace(action="remove"))
        d = self.jload(sp)
        self.assertEqual([h["command"] for e in d["hooks"]["UserPromptSubmit"] for h in e["hooks"]], ["other-tool hook"])
        self.assertNotIn("SessionStart", d["hooks"])
        self.assertIn("PreToolUse", d["hooks"])


class Forget(Base):
    def test_forget_all_removes_everything_and_does_not_reindex(self):
        write_jsonl(self.transcript(), [row("user", "u1", "A long enough message about a private topic that should be forgettable on request.")])
        self.th.do_index()
        with contextlib.redirect_stdout(io.StringIO()):
            self.th.cmd_forget(argparse.Namespace(all=True, session=None, project=None, older_than_days=None))
        self.assertEqual(self.th.do_search("private topic forgettable request"), [])
        self.assertEqual(self.th.do_index(), 0)


if __name__ == "__main__":
    unittest.main()
