"""
Conversa com o memory server da Railway.

  - pack(pasta)        -> zip com manifest.json (sha256, tamanho e mtime de cada arquivo)
  - upload(zip)        -> POST /upload
  - download_latest()  -> GET /latest, extrai em SHARING_MEMORY

Toda requisição é assinada com a chave Ed25519 privada do .env do projeto:
  SERVER_URL=https://<app>.up.railway.app
  CLIENT_ID=pc-casa
  CLIENT_PRIVATE_KEY=<base64>
A chave privada nunca sai do PC: só a assinatura vai na requisição. Para gerar o par
de chaves de um PC novo (a linha pública vai para CLIENT_PUBLIC_KEYS na Railway):
  python src/components/remote_client.py keygen <nome-do-pc>
"""

import base64
import hashlib
import io
import json
import os
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MANIFEST = "manifest.json"
TIMEOUT = 120


class RemoteError(Exception):
    pass


def load_env(path: Path) -> dict:
    """Leitor mínimo de .env (CHAVE=valor, # comentários). Variáveis do sistema têm prioridade."""
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    for key in ("SERVER_URL", "CLIENT_ID", "CLIENT_PRIVATE_KEY"):
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def retry(action, attempts: int = 10, delay: float = 0.5):
    # No Windows, antivírus/indexador/VS Code seguram arquivos recém-criados por um instante
    for i in range(attempts):
        try:
            return action()
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(delay)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_safe_path(name: str) -> bool:
    if not name or len(name) > 512 or "\\" in name or name.startswith("/") or ":" in name.split("/")[0]:
        return False
    return all(part not in ("", ".", "..") for part in name.split("/"))


# ------------------------------------------------------------------ pacote


def pack(folder: Path) -> bytes:
    buf = io.BytesIO()
    files = {}
    # Compressão máxima: o custo de CPU fica no PC, e o server/banco recebem menos bytes
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in sorted(p for p in folder.rglob("*") if p.is_file()):
            rel = path.relative_to(folder).as_posix()
            if rel == MANIFEST:
                continue
            data = path.read_bytes()
            zf.writestr(rel, data)
            files[rel] = {"sha256": sha256(data), "size": len(data), "mtime": path.stat().st_mtime}
        zf.writestr(MANIFEST, json.dumps({"files": files}, indent=2, ensure_ascii=False))
    return buf.getvalue()


def unpack(data: bytes, dest: Path) -> dict:
    """Extrai o zip em dest (via pasta temporária) e devolve o manifesto. Valida tudo antes."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as e:
        raise RemoteError("o server devolveu um zip inválido") from e
    with zf:
        manifest = json.loads(zf.read(MANIFEST).decode("utf-8"))
        names = set(zf.namelist()) - {MANIFEST}
        listed = manifest.get("files") or {}
        if names != set(listed):
            raise RemoteError("zip e manifesto não batem")
        for name in names:
            if not is_safe_path(name):
                raise RemoteError(f"caminho inválido no zip: {name}")

        staging = dest.with_name(dest.name + ".tmp")
        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir(parents=True)
        for name in names:
            target = staging.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(name))
        (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    if dest.exists():
        retry(lambda: shutil.rmtree(dest))
    retry(lambda: staging.rename(dest))
    return manifest


# ------------------------------------------------------------------ http


class Client:
    def __init__(self, env: dict):
        missing = [k for k in ("SERVER_URL", "CLIENT_ID", "CLIENT_PRIVATE_KEY") if not env.get(k)]
        if missing:
            raise RemoteError(f"faltam no .env: {', '.join(missing)}")
        base = env["SERVER_URL"].strip().rstrip("/")
        if base.startswith("http://") and not base.startswith(("http://localhost", "http://127.0.0.1")):
            raise RemoteError("SERVER_URL precisa ser https:// (a memória leva chaves de API)")
        # Aceita só o domínio (como a Railway mostra) e completa com https://
        self.base = base if base.startswith(("https://", "http://")) else f"https://{base}"
        self.client_id = env["CLIENT_ID"]
        seed = base64.b64decode(env["CLIENT_PRIVATE_KEY"])
        if len(seed) != 32:
            raise RemoteError("CLIENT_PRIVATE_KEY precisa ter 32 bytes em base64")
        self.key = Ed25519PrivateKey.from_private_bytes(seed)

    def _request(self, method: str, path: str, body: bytes = b"", content_type: str = None):
        timestamp = str(int(time.time() * 1000))
        # Mesmo formato que o server (auth.js) verifica
        message = f"{method}\n{path}\n{timestamp}\n{sha256(body)}".encode()
        headers = {
            "X-Client-Id": self.client_id,
            "X-Timestamp": timestamp,
            "X-Signature": base64.b64encode(self.key.sign(message)).decode(),
        }
        if content_type:
            headers["Content-Type"] = content_type
        req = urllib.request.Request(self.base + path, data=body or None, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:200]
            raise RemoteError(f"{method} {path} -> HTTP {e.code}: {detail}") from None
        except urllib.error.URLError as e:
            raise RemoteError(f"não consegui falar com {self.base}: {e.reason}") from None

    def upload(self, zip_bytes: bytes) -> dict:
        _, _, body = self._request("POST", "/upload", zip_bytes, "application/zip")
        return json.loads(body)

    def download_latest(self, since=None):
        """Devolve (version_id, zip_bytes) ou None se não há versão mais nova que `since`."""
        path = "/latest" + (f"?since={int(since)}" if since is not None else "")
        status, headers, body = self._request("GET", path)
        if status == 204:
            return None
        return int(headers.get("X-Version-Id", 0)), body


def keygen(client_id: str) -> None:
    """Gera o par Ed25519 de um PC (substitui o antigo npm run keygen do backend)."""
    from cryptography.hazmat.primitives import serialization

    key = Ed25519PrivateKey.generate()
    seed = key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                             serialization.NoEncryption())
    pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    print("# .env deste PC (privado, não compartilhe):")
    print(f"CLIENT_ID={client_id}")
    print(f"CLIENT_PRIVATE_KEY={base64.b64encode(seed).decode()}")
    print("\n# Railway -> CLIENT_PUBLIC_KEYS (pode ser público):")
    print(f"{client_id}:{base64.b64encode(pub).decode()}")


if __name__ == "__main__":
    import re
    import sys

    if len(sys.argv) != 3 or sys.argv[1] != "keygen" or not re.fullmatch(r"[\w.-]{1,64}", sys.argv[2]):
        sys.exit("Uso: python src/components/remote_client.py keygen <nome-do-pc>")
    keygen(sys.argv[2])
