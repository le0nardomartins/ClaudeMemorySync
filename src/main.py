"""
Sincroniza a memória do Claude entre PCs pelo memory server da Railway.

Pastas do projeto:
  SHARING_MEMORY  o que foi RECEBIDO do server (versão mais nova de todos os PCs)
  NEW_MEMORY      o que este PC ENVIA (espelho de ~/.claude/claude_memory_sharing,
                  com o caminho do usuário trocado por ~ nos .md)

Ordem de cada rodada (receber primeiro poupa o server de merges desnecessários):
  1. RECEBER: baixa a versão mais nova (GET /latest) para SHARING_MEMORY, se houver
  2. MESCLAR: quando TODOS os arquivos chegaram, complementa ~/.claude/claude_memory_sharing
     (e settings.json / .claude.json) sem substituir nada
  3. PREPARAR: gera os templates e espelha a pasta do Claude em NEW_MEMORY
  4. ENVIAR: só se NEW_MEMORY tiver algo que o server ainda não tem (POST /upload)

Sem SERVER_URL no .env, faz só o passo 3.

ATENÇÃO: NEW_MEMORY e SHARING_MEMORY contêm chaves de API (env/MCP). Não publique.

Uso:
  python src/main.py                  # uma rodada
  python src/main.py --loop           # roda para sempre, a cada 5 minutos
  python src/main.py --loop --interval 600
  python src/main.py --offline        # só prepara NEW_MEMORY, sem falar com o server
  python src/main.py --no-upload      # só recebe e mescla
"""

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from components import merge_sharing
from components import remote_client as remote
from components import sync_claude_templates as sync_mod
from components.remote_client import retry

PROJECT_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = sync_mod.SHARING_DIR
DEST_DIR = PROJECT_DIR / "NEW_MEMORY"
INCOMING_DIR = merge_sharing.INCOMING_DIR
ENV_FILE = PROJECT_DIR / ".env"
LOCK_FILE = PROJECT_DIR / ".sync.lock"
DEFAULT_INTERVAL = 300

# Lixo que não vale a pena levar junto
IGNORE = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.tmp")


class SyncFailed(Exception):
    pass


def log(msg: str, err: bool = False) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr if err else sys.stdout, flush=True)


def mb(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.2f} MB"


# ------------------------------------------------------------------ preparar NEW_MEMORY


def run_sync() -> None:
    try:
        changed = sync_mod.sync()
    except (json.JSONDecodeError, OSError) as e:
        raise SyncFailed(f"sync falhou: {e.__class__.__name__}: {e}") from None
    log(f"sync: atualizado {', '.join(changed)}" if changed else "sync: nada mudou")


def make_markdown_portable(root: Path) -> None:
    """Troca C:\\Users\\<usuário> por ~ nos .md copiados (os originais ficam intactos)."""
    for md in root.rglob("*.md"):
        try:
            text = md.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        portable = sync_mod.portable_markdown(text)
        if portable != text:
            md.write_text(portable, encoding="utf-8")


def copy_sharing() -> int:
    if not SOURCE_DIR.exists():
        raise SyncFailed(f"Pasta de origem não encontrada: {SOURCE_DIR}")

    # Copia para uma pasta temporária primeiro e só troca no final:
    # se a cópia falhar no meio, NEW_MEMORY continua intacta.
    staging = DEST_DIR.with_name(DEST_DIR.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(SOURCE_DIR, staging, ignore=IGNORE)
    make_markdown_portable(staging)

    # Espelho exato: o que não existe mais no sharing some do NEW_MEMORY
    if DEST_DIR.exists():
        retry(lambda: shutil.rmtree(DEST_DIR))
    retry(lambda: staging.rename(DEST_DIR))
    return sum(1 for _ in DEST_DIR.iterdir())


def refresh_local() -> None:
    run_sync()
    count = copy_sharing()
    log(f"NEW_MEMORY: {count} itens prontos para envio")


# ------------------------------------------------------------------ comparar conteúdo


def fingerprint_folder(folder: Path) -> str:
    """Hash do CONTEÚDO (caminho + sha256 de cada arquivo). Ignora data de modificação."""
    h = hashlib.sha256()
    for path in sorted(p for p in folder.rglob("*") if p.is_file() and p.name != remote.MANIFEST):
        h.update(path.relative_to(folder).as_posix().encode() + b"\0")
        h.update(hashlib.sha256(path.read_bytes()).digest())
    return h.hexdigest()


def fingerprint_manifest(manifest: dict) -> str:
    """Mesmo hash de fingerprint_folder, calculado a partir do manifest.json recebido."""
    h = hashlib.sha256()
    for rel, meta in sorted((manifest.get("files") or {}).items()):
        h.update(rel.encode() + b"\0")
        h.update(bytes.fromhex(meta["sha256"]))
    return h.hexdigest()


# ------------------------------------------------------------------ rodada


def receive(client: "remote.Client") -> None:
    result = client.download_latest(since=merge_sharing.load_state().get("pulled_version"))
    if result is None:
        log("receber: nada novo no server")
    else:
        version, data = result
        manifest = remote.unpack(data, INCOMING_DIR)
        total = sum(f.get("size", 0) for f in manifest.get("files", {}).values())
        merge_sharing.save_state(pulled_version=version, pulled_fingerprint=fingerprint_manifest(manifest))
        log(f"receber: {mb(len(data))} recebidos -> versão {version} ({mb(total)}) em SHARING_MEMORY")

    if merge_sharing.pending():
        changed = merge_sharing.apply()
        if changed:
            log(f"mesclar: {len(changed)} arquivo(s) complementado(s): {', '.join(changed[:8])}"
                + (" ..." if len(changed) > 8 else ""))
        else:
            log("mesclar: nada a complementar")


def send(client: "remote.Client") -> None:
    state = merge_sharing.load_state()
    current = fingerprint_folder(DEST_DIR)
    # Nada a enviar se o server já tem exatamente isso (veio de lá ou já foi enviado)
    if current in (state.get("pulled_fingerprint"), state.get("uploaded_fingerprint")):
        log("enviar: nada novo neste PC")
        return
    package = remote.pack(DEST_DIR)
    saved = client.upload(package)
    merge_sharing.save_state(uploaded_fingerprint=current)
    log(f"enviar: {mb(len(package))} enviados -> versão {saved['versionId']} no server "
        f"({saved['fileCount']} arquivos)")


def run_once(args) -> bool:
    """Uma rodada completa. Devolve False se algo falhou (o loop tenta de novo depois)."""
    try:
        env = remote.load_env(ENV_FILE)
        online = not args.offline and bool(env.get("SERVER_URL"))
        if not args.offline and not online:
            log(f"SERVER_URL não definida em {ENV_FILE}: só preparando NEW_MEMORY")

        client = remote.Client(env) if online else None
        if client:
            receive(client)
        refresh_local()
        if client and not args.no_upload:
            send(client)
        return True
    except (remote.RemoteError, SyncFailed, SystemExit, OSError) as e:
        log(f"[erro] {e}", err=True)
        return False


# ------------------------------------------------------------------ execução


def acquire_lock():
    """Impede duas execuções ao mesmo tempo (ex.: loop agendado + execução manual)."""
    handle = open(LOCK_FILE, "a+")
    try:
        if sys.platform == "win32":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return None
    return handle


def redirect_output_if_hidden() -> None:
    """Rodando via pythonw (sem console), manda a saída para sync.log (máx. ~2 MB)."""
    if sys.stdout is not None:
        return
    log_file = PROJECT_DIR / "sync.log"
    if log_file.exists() and log_file.stat().st_size > 2 * 1024 * 1024:
        log_file.replace(log_file.with_suffix(".log.old"))
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream


def main():
    redirect_output_if_hidden()
    parser = argparse.ArgumentParser(description="Sincroniza a memória do Claude com o memory server.")
    parser.add_argument("--loop", action="store_true", help="roda para sempre, a cada --interval segundos")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="segundos entre rodadas (padrão 300)")
    parser.add_argument("--offline", action="store_true", help="só prepara NEW_MEMORY, sem falar com o server")
    parser.add_argument("--no-upload", action="store_true", help="só recebe e mescla")
    args = parser.parse_args()

    lock = acquire_lock()
    if lock is None:
        log("já existe uma sincronização rodando neste PC; saindo")
        sys.exit(0)

    INCOMING_DIR.mkdir(parents=True, exist_ok=True)

    if not args.loop:
        sys.exit(0 if run_once(args) else 1)

    interval = max(30, args.interval)
    log(f"modo contínuo: sincronizando a cada {interval}s (Ctrl+C para sair)")
    try:
        while True:
            run_once(args)
            time.sleep(interval)
    except KeyboardInterrupt:
        log("encerrado")


if __name__ == "__main__":
    main()
