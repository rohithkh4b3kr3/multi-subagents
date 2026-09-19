"""Shared test helpers. All fixtures are synthetic: no real chats, keys or personal data."""
import importlib.machinery
import importlib.util
import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

BIN = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "bin"))
sys.path.insert(0, BIN)
import token_data  # noqa: E402

USAGE = lambda i=1, cw=0, cr=0, out=0: {"input_tokens": i, "cache_creation_input_tokens": cw,
                                        "cache_read_input_tokens": cr, "output_tokens": out}


def now_ts():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def load_script(name, alias=None):
    """Import bin/<name> (a script without .py) as a fresh module, using the current environment."""
    path = os.path.join(BIN, name)
    loader = importlib.machinery.SourceFileLoader(alias or name.replace("-", "_"), path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def row(role, uuid, content, ts=None, session="sess-1", cwd="/work/proj", usage=None, request_id=None, **extra):
    r = {"type": role, "uuid": uuid, "timestamp": ts or now_ts(), "sessionId": session, "cwd": cwd,
         "message": {"role": role, "content": content}}
    if usage is not None:
        r["message"]["usage"] = usage
        r["requestId"] = request_id or uuid
    r.update(extra)
    return r


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")


class TempHome(unittest.TestCase):
    """Isolated HOME with a fake ~/.claude/projects, so no test can touch the real machine."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = self._tmp.name
        self.projects = os.path.join(self.home, ".claude", "projects")
        os.makedirs(self.projects)
        self._saved = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE", "APPDATA", "TOKEN_HISTORY_DIR", "TOKEN_HISTORY_PROJECTS", "TOKEN_STACK_DEVICE")}
        os.environ.update(HOME=self.home, USERPROFILE=self.home, APPDATA=os.path.join(self.home, "AppData"))
        os.environ["TOKEN_HISTORY_DIR"] = os.path.join(self.home, ".claude", "token-history")
        os.environ["TOKEN_HISTORY_PROJECTS"] = self.projects
        os.environ.pop("TOKEN_STACK_DEVICE", None)
        # token_data fixes its paths at import time, so point them at the sandbox as well.
        self._td = (token_data.ROOT, token_data.CONFIG_DIR, token_data.CONFIG_FILE, token_data.HOME, token_data.CLAUDE)
        token_data.ROOT = self.projects
        token_data.HOME, token_data.CLAUDE = self.home, os.path.join(self.home, ".claude")
        token_data._rtk_cache = (time.time(), None)      # never call the real rtk from tests
        token_data.CONFIG_DIR = os.path.join(self.home, "cfg")
        token_data.CONFIG_FILE = os.path.join(token_data.CONFIG_DIR, "config.json")
        token_data._FILE_CACHE.clear()

    def tearDown(self):
        token_data.ROOT, token_data.CONFIG_DIR, token_data.CONFIG_FILE, token_data.HOME, token_data.CLAUDE = self._td
        token_data._rtk_cache = (0.0, None)
        token_data._FILE_CACHE.clear()
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def transcript(self, name="a.jsonl", project="proj"):
        return os.path.join(self.projects, project, name)
