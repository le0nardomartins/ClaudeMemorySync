"""
Aplica a versão baixada (SHARING_MEMORY) em ~/.claude/claude_memory_sharing.

Só roda quando TODOS os arquivos do manifest.json chegaram com o hash certo.
Nunca substitui nem apaga: complementa.
  - Texto: linhas novas entram, linhas locais ficam. Trecho alterado nos dois
    lados -> ganha o arquivo mais novo (mtime).
  - JSON: chaves novas entram, listas somam sem duplicar, valor divergente ->
    ganha o mais novo.
  - Binário: ganha o mais novo inteiro.
  - .md: compara ignorando a diferença C:\\Users\\<usuário> x ~, para caminho não
    virar "mudança". As linhas locais continuam com o caminho absoluto.
  - settings.template.json / mcp.template.json: mesclados direto no
    ~/.claude/settings.json e nos mcpServers do ~/.claude.json (com {{HOME*}}
    expandido), já que os templates são regerados a partir deles a cada sync.
  - CLAUDE.md: não entra no sharing; substitui inteiro o ~/.claude/CLAUDE.md
    global se for mais novo (backup em CLAUDE.md.bak-sync).

A mesma lógica de merge roda no server (merge.js, na Railway). Mudou uma, mude a outra.

Uso:
  python merge_sharing.py           # aplica se a pasta estiver completa
  python merge_sharing.py --watch   # espera os arquivos chegarem e aplica sozinho
"""

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from difflib import SequenceMatcher
from pathlib import Path, PurePosixPath

try:
    from . import sync_claude_templates as sync_mod
except ImportError:
    import sync_claude_templates as sync_mod

MANIFEST = "manifest.json"
PROJECT_DIR = Path(__file__).resolve().parents[2]
INCOMING_DIR = PROJECT_DIR / "SHARING_MEMORY"
STATE_FILE = PROJECT_DIR / ".sync_state.json"
CONFIG_TEMPLATES = {"settings.template.json", "mcp.template.json"}


# ------------------------------------------------------------------ estado


def load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(**changes) -> None:
    state = load_state()
    state.update(changes)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def read_manifest(folder: Path):
    try:
        return json.loads((folder / MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_complete(folder: Path, manifest: dict) -> bool:
    for rel, meta in (manifest.get("files") or {}).items():
        path = folder.joinpath(*PurePosixPath(rel).parts)
        try:
            if hashlib.sha256(path.read_bytes()).hexdigest() != meta.get("sha256"):
                return False
        except OSError:
            return False
    return True


# ------------------------------------------------------------------ merge


def _bare(line: str) -> str:
    return line.rstrip("\r\n")


def merge_text(local: str, remote: str, remote_newer: bool, normalize=lambda s: s) -> str:
    a = local.splitlines(keepends=True)
    b = remote.splitlines(keepends=True)
    sm = SequenceMatcher(None, [normalize(_bare(x)) for x in a], [normalize(_bare(x)) for x in b], autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            out += a[i1:i2]                      # mantém a linha local (com o caminho local)
        elif tag == "insert":
            out += b[j1:j2]                      # só o remoto tem: complementa
        elif tag == "delete":
            out += a[i1:i2]                      # só o local tem: não apaga
        else:
            out += b[j1:j2] if remote_newer else a[i1:i2]
    for i in range(len(out) - 1):
        if not out[i].endswith("\n"):
            out[i] += "\n"
    return "".join(out)


def merge_json(local, remote, remote_newer: bool):
    if isinstance(local, dict) and isinstance(remote, dict):
        res = dict(local)
        for k, v in remote.items():
            res[k] = merge_json(local[k], v, remote_newer) if k in local else v
        return res
    if isinstance(local, list) and isinstance(remote, list):
        known = {json.dumps(x, sort_keys=True) for x in local}
        return local + [x for x in remote if json.dumps(x, sort_keys=True) not in known]
    if local == remote:
        return local
    return remote if remote_newer else local


def merge_bytes(rel: str, local: bytes, remote: bytes, remote_newer: bool) -> bytes:
    if local == remote:
        return local
    try:
        lt, rt = local.decode("utf-8"), remote.decode("utf-8")
    except UnicodeDecodeError:
        return remote if remote_newer else local

    if rel.lower().endswith(".json"):
        try:
            merged = merge_json(json.loads(lt), json.loads(rt), remote_newer)
            return (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
        except json.JSONDecodeError:
            pass
    normalize = sync_mod.portable_markdown if rel.lower().endswith(".md") else (lambda s: s)
    return merge_text(lt, rt, remote_newer, normalize).encode("utf-8")


def write_atomic(path: Path, data: bytes, mtime: float = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)
    if mtime:
        os.utime(path, (mtime, mtime))


def merge_into(target: Path, data: bytes, remote_mtime: float, rel: str) -> bool:
    """Mescla `data` em `target`. Devolve True se o arquivo mudou."""
    if not target.exists():
        write_atomic(target, data, remote_mtime)
        return True
    local = target.read_bytes()
    local_mtime = target.stat().st_mtime
    merged = merge_bytes(rel, local, data, remote_mtime > local_mtime)
    if merged == local:
        return False
    write_atomic(target, merged, max(local_mtime, remote_mtime))
    return True


def merge_config(target: Path, remote_obj, remote_mtime: float, only_key: str = None) -> bool:
    """Mescla um template (já com {{HOME*}} expandido) num JSON de config real, com backup."""
    current = sync_mod.load_json(target) if target.exists() else {}
    remote_newer = remote_mtime > (target.stat().st_mtime if target.exists() else 0)
    if only_key:
        merged = dict(current)
        merged[only_key] = merge_json(current.get(only_key) or {}, remote_obj.get(only_key) or {}, remote_newer)
    else:
        merged = merge_json(current, remote_obj, remote_newer)
    if merged == current:
        return False
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak-sync"))
    write_atomic(target, (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return True


def replace_claude_md(data: bytes, remote_mtime: float, target: Path = sync_mod.CLAUDE_MD) -> bool:
    """Substitui o CLAUDE.md global inteiro se o recebido for mais novo (com backup)."""
    if target.exists():
        local = target.read_bytes()
        try:
            same = sync_mod.portable_markdown(local.decode("utf-8")).encode("utf-8") == data
        except UnicodeDecodeError:
            same = local == data
        if same or target.stat().st_mtime >= remote_mtime:
            return False
        shutil.copy2(target, target.with_name(target.name + ".bak-sync"))
    write_atomic(target, data, remote_mtime)
    return True


# ------------------------------------------------------------------ aplicar


def apply(folder: Path = INCOMING_DIR, target_dir: Path = sync_mod.SHARING_DIR) -> list:
    manifest = read_manifest(folder)
    if manifest is None:
        raise SystemExit(f"Sem manifest.json em {folder}")
    if not is_complete(folder, manifest):
        raise SystemExit(f"{folder} ainda está incompleta")
    if not target_dir.exists():
        raise SystemExit(f"Pasta do Claude não encontrada: {target_dir}")

    changed = []
    for rel, meta in sorted((manifest.get("files") or {}).items()):
        data = folder.joinpath(*PurePosixPath(rel).parts).read_bytes()
        mtime = float(meta.get("mtime") or 0)

        if rel in CONFIG_TEMPLATES:
            remote = sync_mod.expand_home(json.loads(data.decode("utf-8")))
            if rel == "settings.template.json":
                if merge_config(sync_mod.SETTINGS_SRC, remote, mtime):
                    changed.append("~/.claude/settings.json")
            elif merge_config(sync_mod.CLAUDE_JSON_SRC, remote, mtime, only_key="mcpServers"):
                changed.append("~/.claude.json (mcpServers)")
            continue

        if rel == sync_mod.CLAUDE_MD_NAME:
            if replace_claude_md(data, mtime):
                changed.append("~/.claude/CLAUDE.md")
            continue

        if merge_into(target_dir.joinpath(*PurePosixPath(rel).parts), data, mtime, rel):
            changed.append(rel)

    save_state(applied_version=manifest.get("version"))
    return changed


def pending(folder: Path = INCOMING_DIR) -> bool:
    """True se há uma versão completa em SHARING_MEMORY que ainda não foi aplicada."""
    manifest = read_manifest(folder)
    if manifest is None or not is_complete(folder, manifest):
        return False
    return manifest.get("version") != load_state().get("applied_version")


def report(changed: list) -> None:
    if changed:
        print(f"merge: {len(changed)} arquivo(s) complementado(s)")
        for rel in changed:
            print(f"  + {rel}")
    else:
        print("merge: nada novo")


def main():
    parser = argparse.ArgumentParser(description="Aplica SHARING_MEMORY em ~/.claude/claude_memory_sharing.")
    parser.add_argument("--watch", action="store_true", help="espera a pasta ficar completa e aplica sozinho")
    parser.add_argument("--interval", type=float, default=3.0)
    args = parser.parse_args()

    if not args.watch:
        report(apply())
        return

    print(f"Esperando versões completas em {INCOMING_DIR} (Ctrl+C para sair)")
    try:
        while True:
            if pending():
                report(apply())
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    sys.exit(main())
