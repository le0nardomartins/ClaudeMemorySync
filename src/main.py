"""
Sincroniza a memória do Claude entre PCs.

Pastas do projeto:
  NEW_MEMORY      estado atual deste PC (espelho de ~/.claude/claude_memory_sharing +
                  CLAUDE.md, com o caminho do usuário trocado por ~ nos .md)
  SHARING_MEMORY  BASE: a versão em que este PC e o destino concordaram no último sync

Cada rodada:
  1. PREPARAR: gera os templates de settings/MCP e espelha a pasta do Claude em NEW_MEMORY
  2. SINCRONIZAR (components/sync_engine.py): busca a versão mais nova do destino, faz
     o merge de 3 vias (base x local x remoto), envia o resultado e aplica na pasta do
     Claude. Remoções também se propagam (com trava contra remoção em massa e lixeira).

Destino: o memory server (SERVER_URL, padrão) ou, sem server nenhum, um repositório
Git privado (SYNC_BACKEND=git + GIT_REPO_URL). Com SYNC_KEY no .env, o conteúdo é
criptografado no PC antes de sair (components/crypto_codec.py).
Sem destino no .env, faz só o passo 1.

Não viajam: node_modules, .git, __pycache__ e skills/synced (remote_client.is_excluded).

ATENÇÃO: NEW_MEMORY e SHARING_MEMORY contêm chaves de API (env/MCP). Não publique.

Uso:
  python src/main.py                  # uma rodada
  python src/main.py --loop           # roda para sempre, a cada 15 minutos
  python src/main.py --loop --interval 600
  python src/main.py --offline        # só prepara NEW_MEMORY, sem falar com o destino
  python src/main.py --no-upload      # só recebe e aplica
  python src/main.py --allow-delete   # libera uma remoção grande feita de propósito
"""

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

from components import git_backend
from components import remote_client as remote
from components import sync_engine
from components.crypto_codec import Codec, CodecError
from components import sync_claude_templates as sync_mod
from components.remote_client import retry

PROJECT_DIR = Path(__file__).resolve().parent.parent
SOURCE_DIR = sync_mod.SHARING_DIR
DEST_DIR = PROJECT_DIR / "NEW_MEMORY"
ENV_FILE = PROJECT_DIR / ".env"
LOCK_FILE = PROJECT_DIR / ".sync.lock"
# 15 min: entre as rodadas o server da Railway dorme (sleepApplication) e não cobra
DEFAULT_INTERVAL = 900

# Lixo que não vale a pena levar junto
IGNORE_PATTERNS = shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "*.tmp")


def ignore(dirpath, names):
    """Padrões acima + remote_client.is_excluded (node_modules, skills/synced...)."""
    rel = Path(dirpath).relative_to(SOURCE_DIR).as_posix()
    prefix = "" if rel == "." else rel + "/"
    return set(IGNORE_PATTERNS(dirpath, names)) | {n for n in names if remote.is_excluded(prefix + n)}


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
        data = md.read_bytes()
        portable = sync_mod.portable_bytes(md.name, data)
        if portable != data:
            # Mantém a data de modificação original: é ela que decide quem ganha um conflito
            st = md.stat()
            md.write_bytes(portable)
            os.utime(md, (st.st_atime, st.st_mtime))


def copy_sharing() -> int:
    if not SOURCE_DIR.exists():
        raise SyncFailed(f"Pasta de origem não encontrada: {SOURCE_DIR}")

    # Copia para uma pasta temporária primeiro e só troca no final:
    # se a cópia falhar no meio, NEW_MEMORY continua intacta.
    staging = DEST_DIR.with_name(DEST_DIR.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(SOURCE_DIR, staging, ignore=ignore)
    # O CLAUDE.md global vai junto no pacote, mas não mora no sharing
    if sync_mod.CLAUDE_MD.exists():
        shutil.copy2(sync_mod.CLAUDE_MD, staging / sync_mod.CLAUDE_MD_NAME)
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


# ------------------------------------------------------------------ rodada


def warn_missing_node_modules() -> None:
    """node_modules não viaja: avisa quais skills precisam de npm ci neste PC."""
    for pkg in sorted(SOURCE_DIR.glob("skills/*/package.json")):
        if not (pkg.parent / "node_modules").exists():
            log(f"aviso: rode 'npm ci' em {pkg.parent} (node_modules não é sincronizado)")


def run_once(args) -> bool:
    """Uma rodada completa. Devolve False se algo falhou (o loop tenta de novo depois)."""
    try:
        env = remote.load_env(ENV_FILE)
        use_git = env.get("SYNC_BACKEND", "").strip().lower() == "git"
        online = not args.offline and bool(env.get("GIT_REPO_URL" if use_git else "SERVER_URL"))
        if not args.offline and not online:
            missing = "GIT_REPO_URL" if use_git else "SERVER_URL"
            log(f"{missing} não definida em {ENV_FILE}: só preparando NEW_MEMORY")

        refresh_local()
        if not online:
            return True
        store = git_backend.GitStore(env) if use_git else remote.Client(env)
        codec = Codec(env.get("SYNC_KEY"))
        changed = sync_engine.sync_round(store, codec, DEST_DIR, upload=not args.no_upload,
                                         allow_delete=args.allow_delete, log=log)
        if changed:
            warn_missing_node_modules()
        return True
    except (remote.RemoteError, CodecError, sync_engine.SyncAborted, SyncFailed, SystemExit, OSError) as e:
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


def redirect_output_if_hidden(force: bool = False) -> None:
    """Sem console (pythonw) ou com --log-file (launchd/systemd): saída em sync.log (máx. ~2 MB)."""
    if sys.stdout is not None and not force:
        return
    log_file = PROJECT_DIR / "sync.log"
    if log_file.exists() and log_file.stat().st_size > 2 * 1024 * 1024:
        log_file.replace(log_file.with_suffix(".log.old"))
    stream = open(log_file, "a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream


def main():
    redirect_output_if_hidden()
    parser = argparse.ArgumentParser(description="Sincroniza a memória do Claude entre PCs.")
    parser.add_argument("--loop", action="store_true", help="roda para sempre, a cada --interval segundos")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="segundos entre rodadas (padrão 900)")
    parser.add_argument("--offline", action="store_true", help="só prepara NEW_MEMORY, sem falar com o server")
    parser.add_argument("--no-upload", action="store_true", help="só recebe e aplica")
    parser.add_argument("--allow-delete", action="store_true",
                        help="libera propagar uma remoção grande (padrão: trava acima de 20%% dos arquivos)")
    parser.add_argument("--log-file", action="store_true", help="grava a saída em sync.log (usado pelo autostart)")
    args = parser.parse_args()
    redirect_output_if_hidden(force=args.log_file)

    lock = acquire_lock()
    if lock is None:
        log("já existe uma sincronização rodando neste PC; saindo")
        sys.exit(0)

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
