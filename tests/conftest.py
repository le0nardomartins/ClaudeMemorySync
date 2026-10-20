"""
Harness dos testes: vários "PCs" no mesmo processo, cada um com pastas próprias em
tmp_path, falando com um destino em memória (FakeStore) ou com um repositório Git local.
"""

import json
import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import main  # noqa: E402
from components import remote_client as rc  # noqa: E402
from components import sync_claude_templates as sm  # noqa: E402
from components import sync_engine as eng  # noqa: E402
from components.crypto_codec import Codec, new_key  # noqa: E402


class FakeStore:
    """Destino em memória com a mesma interface do server: versões + blobs + concorrência."""

    def __init__(self):
        self.versions, self.blobs, self.before_put = [], {}, None

    def latest(self, since=None):
        if not self.versions:
            return None, None
        version, manifest = self.versions[-1]
        return (version, None) if version == since else (version, manifest)

    def get_blobs(self, ids):
        return {i: self.blobs[i] for i in ids if i in self.blobs}

    def put(self, base, version, manifest, refs, blobs):
        if self.before_put:
            hook, self.before_put = self.before_put, None
            hook()
        latest = self.versions[-1][0] if self.versions else None
        if latest != base:
            raise rc.Conflict("outro PC enviou antes")
        missing = [i for i in refs if i not in blobs and i not in self.blobs]
        if missing:
            raise rc.MissingOnServer(missing)
        self.blobs.update(blobs)
        self.versions.append((version, manifest))
        return {"versionId": version, "sent": sum(len(b) for b in blobs.values())}


class PC:
    def __init__(self, root: Path, name: str, store, key: str):
        self.name, self.d, self.store, self.codec = name, root / name, store, Codec(key)
        self.sharing = self.d / "sharing"
        self.sharing.mkdir(parents=True)
        self.settings = self.d / "settings.json"
        self.claude_json = self.d / "claude.json"
        self.claude_md = self.d / "CLAUDE.md"
        self.settings.write_text(json.dumps({"permissions": {"allow": ["Read"]}}), encoding="utf-8")
        self.claude_json.write_text(json.dumps({"mcpServers": {}, "numStartups": 3}), encoding="utf-8")
        self.claude_md.write_text("# regras\n", encoding="utf-8")
        self.logs = []

    def activate(self):
        main.SOURCE_DIR = sm.SHARING_DIR = self.sharing
        main.DEST_DIR = self.d / "NEW"
        sm.CLAUDE_MD, sm.SETTINGS_SRC, sm.CLAUDE_JSON_SRC = self.claude_md, self.settings, self.claude_json
        eng.BASE_DIR, eng.CONFLICTS_DIR, eng.TRASH_DIR = self.d / "BASE", self.d / "conflicts", self.d / "trash"

    def run(self, allow_delete=False, upload=True):
        self.activate()
        for name, text in sm.render_templates().items():
            sm.write_if_changed(self.sharing / name, text)
        main.copy_sharing()
        self.logs.clear()
        return eng.sync_round(self.store, self.codec, main.DEST_DIR, upload=upload,
                              allow_delete=allow_delete, log=self.logs.append)

    def path(self, rel):
        return self.sharing / rel

    def write(self, rel, text, mtime=None):
        p = self.path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(text.encode("utf-8"))
        t = mtime if mtime is not None else time.time()
        os.utime(p, (t, t))

    def read(self, rel):
        return self.path(rel).read_bytes().decode("utf-8")

    def conflicts(self):
        return sorted(p.relative_to(self.d / "conflicts").as_posix().split("/", 1)[1]
                      for p in (self.d / "conflicts").rglob("*") if p.is_file())


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def pcs(tmp_path, store):
    """make("pc-a"), make("pc-b")... todos com a mesma SYNC_KEY e o mesmo destino."""
    key = new_key()
    made = {}

    def make(name, *, key_override=None, store_override=None):
        made[name] = PC(tmp_path, name, store_override or store, key_override or key)
        return made[name]

    return make
