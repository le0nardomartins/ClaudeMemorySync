"""
Destino alternativo sem server: um repositório Git PRIVADO faz o papel do server.

Mesma interface do remote_client.Client (latest / get_blobs / put), então o
sync_engine não muda. Como no server, o repositório só guarda dados selados
(cifrados se houver SYNC_KEY): nem o nome dos arquivos aparece.

Layout do repositório de dados:
  meta.json       { "version": N }
  manifest.bin    manifesto selado
  blobs/<id>      conteúdo selado de cada arquivo
  .gitattributes  "* -text": o Git nunca mexe nos bytes

Concorrência: o push só passa se ninguém enviou desde o fetch. Se outro PC enviou
antes, o push é recusado e vira Conflict: o engine busca, mescla e tenta de novo.

.env deste modo:
  SYNC_BACKEND=git
  GIT_REPO_URL=git@github.com:voce/claude-memory-data.git   (repositório PRIVADO e vazio)
  GIT_BRANCH=main                                           (opcional)
  CLIENT_ID=pc-notebook                                     (opcional: nome nos commits)
  SYNC_KEY=...                                              (recomendado)
"""

import json
import os
import re
import shutil
import socket
import subprocess
from pathlib import Path

try:
    from . import remote_client as remote
except ImportError:
    import remote_client as remote

PROJECT_DIR = Path(__file__).resolve().parents[2]
WORK_DIR = PROJECT_DIR / ".sync_repo"
ID_RE = re.compile(r"^[0-9a-f]{64}$")


class GitStore:
    def __init__(self, env: dict, work_dir: Path = WORK_DIR):
        if not env.get("GIT_REPO_URL"):
            raise remote.RemoteError("faltam no .env: GIT_REPO_URL")
        if shutil.which("git") is None:
            raise remote.RemoteError("git não encontrado no PATH")
        self.url = env["GIT_REPO_URL"].strip()
        self.branch = (env.get("GIT_BRANCH") or "main").strip()
        self.client_id = env.get("CLIENT_ID") or socket.gethostname()
        self.dir = Path(work_dir)

    # -------------------------------------------------------------- git

    def _git(self, *args, check=True, cwd=None):
        env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        proc = subprocess.run(["git", *args], cwd=cwd or self.dir, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
        if check and proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip().splitlines()
            raise remote.RemoteError(f"git {args[0]} falhou: {detail[-1] if detail else proc.returncode}")
        return proc

    def _sync(self) -> None:
        """Deixa o clone local idêntico ao remoto (o clone é só um cache: nada local se perde)."""
        if not (self.dir / ".git").exists():
            if self.dir.exists():
                shutil.rmtree(self.dir)
            self.dir.parent.mkdir(parents=True, exist_ok=True)
            self._git("clone", "--quiet", self.url, str(self.dir), cwd=self.dir.parent)
            self._git("config", "core.autocrlf", "false")
            self._git("config", "core.longpaths", "true")
        self._git("fetch", "--quiet", "origin")
        if self._git("rev-parse", "--verify", "--quiet", f"origin/{self.branch}", check=False).returncode == 0:
            self._git("checkout", "--quiet", "-B", self.branch, f"origin/{self.branch}")
            self._git("reset", "--quiet", "--hard", f"origin/{self.branch}")
        else:
            # Repositório vazio: começa a branch do zero (descarta commit local de um push que falhou)
            self._git("symbolic-ref", "HEAD", f"refs/heads/{self.branch}")
            self._git("update-ref", "-d", f"refs/heads/{self.branch}", check=False)
            self._git("read-tree", "--empty")
        self._git("clean", "-fdq")

    def _version(self):
        try:
            return int(json.loads((self.dir / "meta.json").read_text(encoding="utf-8"))["version"])
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _blob(self, blob_id: str) -> Path:
        if not ID_RE.match(blob_id):
            raise remote.RemoteError(f"id de blob inválido: {blob_id[:80]}")
        return self.dir / "blobs" / blob_id

    # -------------------------------------------------------------- interface de destino

    def latest(self, since=None):
        self._sync()
        version = self._version()
        if version is None or version == since:
            return version, None
        return version, (self.dir / "manifest.bin").read_bytes()

    def get_blobs(self, ids: list) -> dict:
        out = {}
        for blob_id in ids:
            path = self._blob(blob_id)
            if path.exists():
                out[blob_id] = path.read_bytes()
        return out

    def put(self, base, version: int, manifest: bytes, refs: set, blobs: dict) -> dict:
        self._sync()
        if self._version() != base:
            raise remote.Conflict("outro PC enviou antes")
        missing = [i for i in refs if i not in blobs and not self._blob(i).exists()]
        if missing:
            raise remote.MissingOnServer(missing)

        (self.dir / "blobs").mkdir(exist_ok=True)
        written = 0
        for blob_id in refs:
            if blob_id in blobs and not self._blob(blob_id).exists():
                self._blob(blob_id).write_bytes(blobs[blob_id])
                written += len(blobs[blob_id])
        for old in (self.dir / "blobs").iterdir():
            if old.name not in refs:
                old.unlink()
        (self.dir / "manifest.bin").write_bytes(manifest)
        (self.dir / "meta.json").write_text(json.dumps({"version": version}) + "\n", encoding="utf-8")
        (self.dir / ".gitattributes").write_text("* -text\n", encoding="utf-8")

        self._git("add", "-A")
        self._git("-c", f"user.name={self.client_id}", "-c", f"user.email={self.client_id}@claude-memory-sync.local",
                  "commit", "--quiet", "--no-verify", "-m", f"sync: {self.client_id} -> versão {version}")
        push = self._git("push", "--quiet", "origin", f"HEAD:refs/heads/{self.branch}", check=False)
        if push.returncode != 0:
            detail = push.stderr.lower()
            if any(s in detail for s in ("rejected", "fetch first", "non-fast-forward", "stale info")):
                raise remote.Conflict("push recusado: outro PC enviou antes")
            lines = push.stderr.strip().splitlines()
            raise remote.RemoteError(f"git push falhou: {lines[-1] if lines else push.returncode}")
        return {"versionId": version, "sent": written}
