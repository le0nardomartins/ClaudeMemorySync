"""
Conversa com o memory server da Railway.

O server guarda cada CONTEÚDO uma vez só (pelo sha256) e cada versão é só um
manifesto (caminho -> sha256, tamanho, mtime). Por isso só viaja o que mudou:
  - scan(pasta)          -> manifesto + índice sha256 -> arquivo local
  - Client.upload()      -> POST /upload com o manifesto e só o conteúdo que o
                            server ainda não tem (se ele pedir mais, reenvia)
  - Client.latest()      -> GET /latest: só o manifesto da versão mais nova
  - materialize()        -> monta SHARING_MEMORY com o que já existe neste PC e
                            baixa (POST /blobs) só o que falta

Formato "bundle" (igual ao bundle.js do server): sequência de registros
  [4 bytes: tamanho do nome][nome utf-8][4 bytes: tamanho dos dados][dados deflate-raw]
com nome "manifest.json" ou o sha256 do conteúdo descompactado.

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
import json
import os
import shutil
import struct
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path, PurePosixPath

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

MANIFEST = "manifest.json"
TIMEOUT = 120
BUNDLE_TYPE = "application/octet-stream"
MAX_UNCOMPRESSED = 200 * 1024 * 1024
FETCH_BATCH = 500

# Nunca viajam: dependências reinstaláveis (npm ci), lixo de ferramenta e as skills
# que o app Claude Desktop gerencia sozinho em cada PC. Mesma regra no bundle.js do server.
EXCLUDED_DIRS = {"node_modules", ".git", "__pycache__"}
EXCLUDED_PREFIXES = ("skills/synced/",)


class RemoteError(Exception):
    pass


class MissingOnServer(RemoteError):
    def __init__(self, missing):
        super().__init__(f"o server pediu {len(missing)} arquivo(s) a mais")
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


def is_excluded(rel: str) -> bool:
    """Caminho posix relativo à raiz do sharing que não deve ser enviado nem recebido."""
    return any(part in EXCLUDED_DIRS for part in rel.split("/")) or (rel + "/").startswith(EXCLUDED_PREFIXES)


# ------------------------------------------------------------------ bundle


def deflate(data: bytes) -> bytes:
    # Compressão máxima: o custo de CPU fica no PC, e o server/banco recebem menos bytes
    c = zlib.compressobj(9, zlib.DEFLATED, -15)
    return c.compress(data) + c.flush()


def inflate(data: bytes, limit: int = MAX_UNCOMPRESSED) -> bytes:
    d = zlib.decompressobj(-15)
    try:
        out = d.decompress(data, limit)
    except zlib.error as e:
        raise RemoteError(f"dados compactados inválidos: {e}") from None
    if d.unconsumed_tail:
        raise RemoteError("conteúdo grande demais depois de descompactado")
    return out


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


# ------------------------------------------------------------------ manifesto


def scan(folder: Path):
    """Devolve (manifesto, índice sha256 -> Path) de uma pasta, sem as exclusões."""
    files, index = {}, {}
    for path in sorted(p for p in folder.rglob("*") if p.is_file()):
        rel = path.relative_to(folder).as_posix()
        if rel == MANIFEST or is_excluded(rel):
            continue
        data = path.read_bytes()
        digest = sha256(data)
        files[rel] = {"sha256": digest, "size": len(data), "mtime": path.stat().st_mtime}
        index.setdefault(digest, path)
    return {"files": files}, index


def manifest_index(folder: Path) -> dict:
    """Índice sha256 -> Path a partir do manifest.json já gravado na pasta (sem reler tudo)."""
    try:
        manifest = json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return {m["sha256"]: folder.joinpath(*PurePosixPath(rel).parts)
            for rel, m in (manifest.get("files") or {}).items()}


def read_verified(path: Path, digest: str):
    """Conteúdo do arquivo se ele ainda tiver esse hash; senão None."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if sha256(data) == digest else None


def materialize(manifest: dict, dest: Path, sources: list, client: "Client") -> int:
    """
    Monta dest com os arquivos do manifesto (via pasta temporária). O conteúdo vem,
    nessa ordem, do manifest.json antigo de dest, das outras pastas em `sources`
    (lidas só se ainda faltar algo) e, por último, do server. Devolve os bytes baixados.
    """
    files = manifest.get("files") or {}
    for rel in files:
        if not is_safe_path(rel):
            raise RemoteError(f"caminho inválido no manifesto: {rel}")

    wanted = {m["sha256"] for m in files.values()}
    found = {}

    def take(index):
        for digest, path in index.items():
            if digest in wanted and digest not in found:
                data = read_verified(path, digest)
                if data is not None:
                    found[digest] = data

    take(manifest_index(dest))
    for folder in sources:
        if len(found) == len(wanted):
            break
        if folder.exists():
            take(scan(folder)[1])

    missing = sorted(wanted - set(found))
    downloaded = 0
    for i in range(0, len(missing), FETCH_BATCH):
        blobs, size = client.fetch_blobs(missing[i:i + FETCH_BATCH])
        found.update(blobs)
        downloaded += size
    still = wanted - set(found)
    if still:
        raise RemoteError(f"o server não tem {len(still)} conteúdo(s) do manifesto")

    staging = dest.with_name(dest.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for rel, meta in files.items():
        target = staging.joinpath(*PurePosixPath(rel).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(found[meta["sha256"]])
    (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    if dest.exists():
        retry(lambda: shutil.rmtree(dest))
    retry(lambda: staging.rename(dest))
    return downloaded


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
                    raise MissingOnServer(json.loads(detail)["missing"]) from None
                except (ValueError, KeyError):
                    pass
            raise RemoteError(f"{method} {path} -> HTTP {e.code}: {detail[:200]}") from None
        except urllib.error.URLError as e:
            raise RemoteError(f"não consegui falar com {self.base}: {e.reason}") from None

    def upload(self, manifest: dict, index: dict, known: set):
        """
        Envia o manifesto e o conteúdo que não está em `known` (hashes que o server
        já deve ter). Se o server pedir mais (409), reenvia uma vez com o que faltou.
        Devolve (resposta do server, bytes enviados).
        """
        send = {m["sha256"] for m in manifest["files"].values()} - known
        head = (MANIFEST, deflate(json.dumps(manifest, ensure_ascii=False).encode("utf-8")))
        for attempt in range(2):
            records = [head]
            for digest in sorted(send):
                data = read_verified(index[digest], digest)
                if data is None:
                    raise RemoteError(f"{index[digest].name} mudou durante o envio; fica para a próxima rodada")
                records.append((digest, deflate(data)))
            body = encode_bundle(records)
            try:
                _, _, resp = self._request("POST", "/upload", body, BUNDLE_TYPE)
                return json.loads(resp), len(body)
            except MissingOnServer as e:
                extra = set(e.missing) - send
                if attempt or not extra or not extra <= set(index):
                    raise RemoteError(f"o server continua sem {len(e.missing)} conteúdo(s)") from None
                send |= extra

    def latest(self, since=None):
        """Devolve (version_id, manifesto) ou None se não há versão mais nova que `since`."""
        path = "/latest" + (f"?since={int(since)}" if since is not None else "")
        status, headers, body = self._request("GET", path)
        if status == 204:
            return None
        records = decode_bundle(body)
        if len(records) != 1 or records[0][0] != MANIFEST:
            raise RemoteError("o server devolveu um manifesto inválido (server desatualizado?)")
        manifest = json.loads(inflate(records[0][1], 5 * 1024 * 1024).decode("utf-8"))
        # O proxy da Railway pode mandar o nome do header em minúsculas
        version = next((v for k, v in headers.items() if k.lower() == "x-version-id"), 0)
        return int(version), manifest

    def fetch_blobs(self, hashes: list):
        """Baixa conteúdos pelo hash. Devolve ({sha: bytes}, bytes trafegados)."""
        body = json.dumps({"hashes": hashes}).encode()
        _, _, data = self._request("POST", "/blobs", body, "application/json")
        out = {}
        for name, packed in decode_bundle(data):
            content = inflate(packed)
            if name in hashes and sha256(content) == name:
                out[name] = content
        return out, len(data)


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
