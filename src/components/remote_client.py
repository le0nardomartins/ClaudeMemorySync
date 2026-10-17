"""
Conversa com o memory server (Railway) e funções comuns aos dois destinos.

O server é só um ARMAZÉM: guarda versões (manifesto cifrado + lista de blobs) e
blobs cifrados. Quem faz o merge é o PC (sync_engine.py). Para dois PCs não se
atropelarem, cada envio diz em qual versão se baseou (`base`); se o server já
estiver numa versão mais nova, responde 409 e o PC busca, mescla e tenta de novo.

Interface de destino (igual em git_backend.GitStore):
  latest(since)  -> (versão mais nova | None, manifesto selado | None se for a própria `since`)
  get_blobs(ids) -> {id: blob selado}
  put(base, version, manifest, refs, blobs) -> dict   (Conflict / MissingOnServer)

Formato "bundle" (igual ao bundle.js do server): sequência de registros
  [4 bytes: tamanho do nome][nome utf-8][4 bytes: tamanho dos dados][dados]

Toda requisição é assinada com a chave Ed25519 privada do .env do projeto:
  SERVER_URL=https://<app>.up.railway.app
  CLIENT_ID=pc-casa
  CLIENT_PRIVATE_KEY=<base64>
  SYNC_KEY=<base64>            (recomendado: criptografa o conteúdo, igual em todos os PCs)

Gerar chaves:
  python src/components/remote_client.py keygen <nome-do-pc>   par Ed25519 deste PC
  python src/components/remote_client.py synckey               SYNC_KEY (uma vez, copie para todos os PCs)
"""

import base64
import hashlib
import json
import os
import struct
import time
import urllib.error
import urllib.request
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MANIFEST = "manifest.json"
TIMEOUT = 120
BUNDLE_TYPE = "application/octet-stream"
FETCH_BATCH = 500
ENV_KEYS = ("SERVER_URL", "CLIENT_ID", "CLIENT_PRIVATE_KEY", "SYNC_KEY", "SYNC_BACKEND", "GIT_REPO_URL", "GIT_BRANCH")

# Nunca viajam: dependências reinstaláveis (npm ci), lixo de ferramenta e as skills
# que o app Claude Desktop gerencia sozinho em cada PC.
EXCLUDED_DIRS = {"node_modules", ".git", "__pycache__"}
EXCLUDED_PREFIXES = ("skills/synced/",)


class RemoteError(Exception):
    pass


class Conflict(RemoteError):
    """O destino já tem uma versão mais nova que a `base` do envio."""


class MissingOnServer(RemoteError):
    def __init__(self, missing):
        super().__init__(f"o destino pediu {len(missing)} blob(s) a mais")
        self.missing = missing


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
    for key in ENV_KEYS:
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


def is_excluded(rel: str) -> bool:
    """Caminho posix relativo à raiz do sharing que não deve ser enviado nem recebido."""
    return any(part in EXCLUDED_DIRS for part in rel.split("/")) or (rel + "/").startswith(EXCLUDED_PREFIXES)


def scan(folder: Path):
    """Devolve ({caminho: {sha256, size}}, índice sha256 -> Path) de uma pasta, sem as exclusões."""
    files, index = {}, {}
    if not folder.exists():
        return files, index
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel == MANIFEST or is_excluded(rel):
            continue
        data = path.read_bytes()
        digest = sha256(data)
        files[rel] = {"sha256": digest, "size": len(data)}
        index.setdefault(digest, path)
    return files, index


def read_verified(path: Path, digest: str):
    """Conteúdo do arquivo se ele ainda tiver esse hash; senão None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if sha256(data) == digest else None


# ------------------------------------------------------------------ bundle


def encode_bundle(records) -> bytes:
    parts = []
    for name, data in records:
        n = name.encode("utf-8")
        parts += [struct.pack(">I", len(n)), n, struct.pack(">I", len(data)), data]
    return b"".join(parts)


def decode_bundle(data: bytes) -> list:
    records, pos = [], 0
    while pos < len(data):
        if pos + 4 > len(data):
            raise RemoteError("bundle truncado")
        (name_len,) = struct.unpack_from(">I", data, pos)
        pos += 4
        name = data[pos:pos + name_len].decode("utf-8")
        pos += name_len
        if pos + 4 > len(data):
            raise RemoteError("bundle truncado")
        (data_len,) = struct.unpack_from(">I", data, pos)
        pos += 4
        if pos + data_len > len(data):
            raise RemoteError("bundle truncado")
        records.append((name, data[pos:pos + data_len]))
        pos += data_len
    return records


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
            detail = e.read().decode("utf-8", "replace")
            if e.code == 409:
                try:
                    info = json.loads(detail)
                except ValueError:
                    info = {}
                if info.get("conflict"):
                    raise Conflict("outro PC enviou antes") from None
                if "missing" in info:
                    raise MissingOnServer(info["missing"]) from None
            raise RemoteError(f"{method} {path} -> HTTP {e.code}: {detail[:200]}") from None
        except urllib.error.URLError as e:
            raise RemoteError(f"não consegui falar com {self.base}: {e.reason}") from None

    def latest(self, since=None):
        path = "/latest" + (f"?since={int(since)}" if since is not None else "")
        status, headers, body = self._request("GET", path)
        # O proxy da Railway pode mandar o nome do header em minúsculas
        raw = next((v for k, v in headers.items() if k.lower() == "x-version-id"), "")
        version = int(raw) if raw.strip().isdigit() else None
        if status == 204:
            return version, None
        records = decode_bundle(body)
        if len(records) != 1 or records[0][0] != "manifest":
            raise RemoteError("o server devolveu um manifesto inválido (server desatualizado?)")
        return version, records[0][1]

    def get_blobs(self, ids: list) -> dict:
        out = {}
        for i in range(0, len(ids), FETCH_BATCH):
            body = json.dumps({"ids": ids[i:i + FETCH_BATCH]}).encode()
            _, _, data = self._request("POST", "/blobs", body, "application/json")
            out.update(decode_bundle(data))
        return out

    def put(self, base, version: int, manifest: bytes, refs: set, blobs: dict) -> dict:
        meta = json.dumps({"base": base, "version": version, "refs": sorted(refs)}).encode()
        records = [("meta.json", meta), ("manifest", manifest), *sorted(blobs.items())]
        body = encode_bundle(records)
        _, _, resp = self._request("POST", "/upload", body, BUNDLE_TYPE)
        return {**json.loads(resp), "sent": len(body)}


def keygen(client_id: str) -> None:
    """Gera o par Ed25519 de um PC."""
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

    if len(sys.argv) == 2 and sys.argv[1] == "synckey":
        try:
            from .crypto_codec import new_key
        except ImportError:
            from crypto_codec import new_key
        print("# Coloque a MESMA linha no .env de TODOS os seus PCs (secreta):")
        print(f"SYNC_KEY={new_key()}")
    elif len(sys.argv) == 3 and sys.argv[1] == "keygen" and re.fullmatch(r"[\w.-]{1,64}", sys.argv[2]):
        keygen(sys.argv[2])
    else:
        sys.exit("Uso: python src/components/remote_client.py keygen <nome-do-pc> | synckey")
