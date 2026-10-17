"""
Uma rodada de sincronização, com merge de 3 vias feito NO PC.

  BASE    SHARING_MEMORY/  a versão em que este PC e o destino concordaram da última vez
  LOCAL   NEW_MEMORY/      o estado atual deste PC (já em formato portátil)
  REMOTO  destino          a versão mais nova (server ou repositório Git)

Para cada arquivo, compara local e remoto com a base (merge3.py):
  - mudou só de um lado        -> entra essa mudança, inclusive REMOÇÃO
  - mudou dos dois lados       -> merge por linha/chave; conflito real -> ganha este PC
                                  (é o último a chegar na ordem do destino) e a versão
                                  perdedora vai para .sync_conflicts/
Depois envia o resultado dizendo em qual versão se baseou. Se outro PC enviou no meio
do caminho (Conflict), busca de novo e refaz o merge. Só então aplica na pasta do Claude:
arquivo removido vai para .sync_trash/, nunca é apagado de vez.

A ordem das mudanças é a ordem das versões no destino, não o relógio de cada PC.
"""

import json
import shutil
import time
from pathlib import Path, PurePosixPath

try:
    from . import merge3
    from . import remote_client as remote
    from . import sync_claude_templates as sync_mod
except ImportError:
    import merge3
    import remote_client as remote
    import sync_claude_templates as sync_mod

PROJECT_DIR = Path(__file__).resolve().parents[2]
BASE_DIR = PROJECT_DIR / "SHARING_MEMORY"
CONFLICTS_DIR = PROJECT_DIR / ".sync_conflicts"
TRASH_DIR = PROJECT_DIR / ".sync_trash"
CONFIG_TEMPLATES = {"settings.template.json", "mcp.template.json"}
MANIFEST_FORMAT = 2
MAX_ATTEMPTS = 5
MAX_MANIFEST = 20 * 1024 * 1024


class SyncAborted(Exception):
    pass


def mass_delete_limit(total: int) -> int:
    # Proteção contra pasta sumida/junction quebrada virar "apague tudo em todos os PCs"
    return max(10, total // 5)


# ------------------------------------------------------------------ conteúdo


class Contents:
    """Conteúdo por sha256: do disco (base/local, conferido pelo hash), do destino ou do merge."""

    def __init__(self, store, codec, *indexes):
        self.store, self.codec, self.indexes, self.cache = store, codec, indexes, {}

    def add(self, data: bytes) -> str:
        digest = remote.sha256(data)
        self.cache[digest] = data
        return digest

    def _local(self, digest):
        for index in self.indexes:
            if digest in index:
                data = remote.read_verified(index[digest], digest)
                if data is not None:
                    self.cache[digest] = data
                    return data
        return None

    def fetch(self, entries) -> int:
        """entries: [(sha256, id)] do manifesto remoto. Baixa só o que não existe aqui."""
        want = {}
        for digest, blob_id in entries:
            if digest not in self.cache and self._local(digest) is None:
                want[blob_id] = digest
        if not want:
            return 0
        size = 0
        for blob_id, sealed in self.store.get_blobs(sorted(want)).items():
            if blob_id not in want:
                continue
            data = self.codec.open(sealed, aad=blob_id)
            if remote.sha256(data) != want[blob_id]:
                raise remote.RemoteError(f"blob {blob_id[:12]} não confere com o manifesto")
            self.cache[want[blob_id]] = data
            size += len(sealed)
        lacking = [d for d in want.values() if d not in self.cache]
        if lacking:
            raise remote.RemoteError(f"o destino não tem {len(lacking)} conteúdo(s) do manifesto")
        return size

    def get(self, digest):
        if digest is None:
            return None
        data = self.cache[digest] if digest in self.cache else self._local(digest)
        if data is None:
            raise remote.RemoteError(f"conteúdo {digest[:12]} indisponível")
        return data


# ------------------------------------------------------------------ base


def load_base(folder: Path = None) -> dict:
    """{version, codec, files: {caminho: {sha256, size[, id]}}}. Formato antigo: reescaneia."""
    folder = folder or BASE_DIR
    try:
        manifest = json.loads((folder / remote.MANIFEST).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = None
    if manifest and manifest.get("format") == MANIFEST_FORMAT:
        return manifest
    # Sem base ou base do protocolo antigo: o conteúdo serve de base, mas a versão não
    # vale no destino novo (força buscar o manifesto)
    files, _ = remote.scan(folder)
    return {"format": MANIFEST_FORMAT, "version": None, "codec": None, "files": files}


def save_base(base: dict, contents: Contents, folder: Path = None) -> None:
    folder = folder or BASE_DIR
    staging = folder.with_name(folder.name + ".tmp")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    for rel, meta in base["files"].items():
        target = staging.joinpath(*PurePosixPath(rel).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(contents.get(meta["sha256"]))
    (staging / remote.MANIFEST).write_text(json.dumps(base, indent=2, ensure_ascii=False), encoding="utf-8")
    if folder.exists():
        remote.retry(lambda: shutil.rmtree(folder))
    remote.retry(lambda: staging.rename(folder))


# ------------------------------------------------------------------ merge


def merge_snapshots(base: dict, local: dict, remote_files: dict, contents: Contents):
    """Mapas caminho -> meta. Devolve (caminho -> sha256 do resultado, conflitos, removidos aqui)."""
    sha = lambda m, p: (m.get(p) or {}).get("sha256")
    merged, conflicts, local_deleted, both = {}, [], [], []
    for rel in sorted(set(base) | set(local) | set(remote_files)):
        b, l, r = sha(base, rel), sha(local, rel), sha(remote_files, rel)
        if l == r:
            result = l
        elif l == b:
            result = r
        elif r == b:
            result = l
            if l is None:
                local_deleted.append(rel)
        else:
            both.append(rel)
            continue
        if result is not None:
            merged[rel] = result

    contents.fetch([(remote_files[p]["sha256"], remote_files[p]["id"]) for p in both if p in remote_files])
    for rel in both:
        b, l, r = sha(base, rel), sha(local, rel), sha(remote_files, rel)
        data, conflict = merge3.merge_file(rel, contents.get(b), contents.get(l), contents.get(r),
                                           sort_json=rel in CONFIG_TEMPLATES)
        if data is not None:
            merged[rel] = contents.add(data)
        if conflict:
            conflicts.append((rel, contents.get(r)))
    return merged, conflicts, local_deleted


# ------------------------------------------------------------------ aplicar na pasta do Claude


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    remote.retry(lambda: tmp.replace(path))


def _patch_config(target: Path, before: dict, after: dict, only_key: str = None) -> bool:
    """Aplica no config REAL só a diferença entre os templates (before -> after), com backup."""
    current = sync_mod.load_json(target) if target.exists() else {}
    if only_key:
        part, _ = merge3.merge3_json(before.get(only_key) or {}, current.get(only_key) or {}, after.get(only_key) or {})
        merged = {**current, only_key: part}
    else:
        merged, _ = merge3.merge3_json(before, current, after)
    if merged == current:
        return False
    if target.exists():
        shutil.copy2(target, target.with_name(target.name + ".bak-sync"))
    _write(target, (json.dumps(merged, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return True


def apply_local(merged: dict, local: dict, contents: Contents, stamp: str) -> list:
    changed = []
    for rel in sorted(set(merged) | set(local)):
        new, old = merged.get(rel), (local.get(rel) or {}).get("sha256")
        if new == old:
            continue
        if rel in CONFIG_TEMPLATES:
            if new is None:
                continue
            before = sync_mod.expand_home(json.loads(contents.get(old))) if old else {}
            after = sync_mod.expand_home(json.loads(contents.get(new)))
            if rel == "settings.template.json":
                touched = _patch_config(sync_mod.SETTINGS_SRC, before, after)
            else:
                touched = _patch_config(sync_mod.CLAUDE_JSON_SRC, before, after, only_key="mcpServers")
            if touched:
                changed.append(rel)
            continue
        if rel == sync_mod.CLAUDE_MD_NAME:
            if new is not None:
                if sync_mod.CLAUDE_MD.exists():
                    shutil.copy2(sync_mod.CLAUDE_MD, sync_mod.CLAUDE_MD.with_name("CLAUDE.md.bak-sync"))
                _write(sync_mod.CLAUDE_MD, contents.get(new))
                changed.append(rel)
            continue
        target = sync_mod.SHARING_DIR.joinpath(*PurePosixPath(rel).parts)
        if new is None:
            if target.exists():
                trash = TRASH_DIR / stamp / Path(*PurePosixPath(rel).parts)
                trash.parent.mkdir(parents=True, exist_ok=True)
                remote.retry(lambda: shutil.move(str(target), str(trash)))
            changed.append(f"-{rel}")
        else:
            _write(target, contents.get(new))
            changed.append(rel)
    return changed


# ------------------------------------------------------------------ rodada


def sync_round(store, codec, local_dir: Path, *, upload=True, allow_delete=False, log=print) -> list:
    """Devolve a lista de caminhos alterados neste PC (vazia se nada mudou aqui)."""
    local, local_index = remote.scan(local_dir)
    base = load_base()
    base_index = {m["sha256"]: BASE_DIR.joinpath(*PurePosixPath(p).parts) for p, m in base["files"].items()}
    same = lambda a, b: a.keys() == b.keys() and all(a[p]["sha256"] == b[p]["sha256"] for p in a)

    for attempt in range(MAX_ATTEMPTS):
        contents = Contents(store, codec, local_index, base_index)
        remote_version, sealed = store.latest(since=base["version"])
        if sealed is None:
            # Nada novo no destino (ou destino vazio): o remoto é a própria base
            if remote_version is not None and remote_version == base["version"] and same(local, base["files"]):
                log("sync: nada mudou")
                return []
            remote_m = {"format": MANIFEST_FORMAT, "version": remote_version, "codec": base["codec"], "files": base["files"]}
        else:
            remote_m = json.loads(codec.open(sealed, aad="manifest", limit=MAX_MANIFEST))
        remote_files = remote_m["files"]
        for rel in remote_files:
            if not remote.is_safe_path(rel):
                raise remote.RemoteError(f"caminho inválido no manifesto: {rel}")

        merged, conflicts, local_deleted = merge_snapshots(base["files"], local, remote_files, contents)
        if len(local_deleted) > mass_delete_limit(len(base["files"])) and not allow_delete:
            raise SyncAborted(f"{len(local_deleted)} arquivos sumiram deste PC desde o último sync "
                              f"(ex.: {', '.join(local_deleted[:3])}). Se foi de propósito, rode "
                              f"python src/main.py --allow-delete; senão, confira as junctions de ~/.claude")

        downloaded = contents.fetch([(m["sha256"], m["id"]) for m in remote_files.values() if "id" in m])
        # Destino vazio (primeiro PC, ou destino recriado): manda tudo, mesmo sem mudança local
        upload_needed = ((remote_version is None and bool(merged))
                         or not same({p: {"sha256": s} for p, s in merged.items()}, remote_files))
        new_version = remote_version if remote_version is not None else base["version"]

        if upload_needed and upload:
            # Reaproveita ids que o destino já tem (mesmo formato de selagem)
            known = {}
            for m in (remote_m, base):
                if m.get("codec") == codec.name and remote_version is not None:
                    known.update({f["sha256"]: f["id"] for f in m["files"].values() if "id" in f})
            files = {}
            for rel, digest in merged.items():
                data = contents.get(digest)
                files[rel] = {"sha256": digest, "size": len(data), "id": known.get(digest) or codec.blob_id(data)}
            new_version = (remote_version or 0) + 1
            new_base = {"format": MANIFEST_FORMAT, "version": new_version, "codec": codec.name, "files": files}
            sealed_manifest = codec.seal(json.dumps(new_base, ensure_ascii=False).encode("utf-8"), aad="manifest")
            refs = {f["id"] for f in files.values()}
            by_id = {f["id"]: f["sha256"] for f in files.values()}
            on_server = set(known.values())
            blobs = {i: codec.seal(contents.get(d), aad=i) for i, d in by_id.items() if i not in on_server}
            try:
                try:
                    result = store.put(remote_version, new_version, sealed_manifest, refs, blobs)
                except remote.MissingOnServer as e:
                    blobs.update({i: codec.seal(contents.get(by_id[i]), aad=i) for i in e.missing if i in by_id})
                    result = store.put(remote_version, new_version, sealed_manifest, refs, blobs)
            except remote.Conflict:
                log(f"enviar: outro PC enviou antes; buscando de novo ({attempt + 1}/{MAX_ATTEMPTS})")
                continue
            log(f"enviar: versão {new_version} ({len(files)} arquivos), {_mb(result.get('sent', 0))} enviados")
        elif upload_needed:
            new_base = remote_m          # --no-upload: a base fica no remoto para o envio sair depois
        else:
            new_base = {**remote_m, "format": MANIFEST_FORMAT, "version": new_version}

        stamp = time.strftime("%Y%m%d-%H%M%S")
        for rel, loser in conflicts:
            target = CONFLICTS_DIR / stamp / Path(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(loser)
        if conflicts:
            log(f"conflito em {len(conflicts)} arquivo(s), ficou a versão deste PC; a outra está em "
                f"{CONFLICTS_DIR / stamp}: {', '.join(r for r, _ in conflicts[:5])}")

        changed = apply_local(merged, local, contents, stamp)
        save_base(new_base, contents)
        if changed:
            log(f"receber: {len(changed)} mudança(s) aplicada(s) ({_mb(downloaded)} baixados): "
                f"{', '.join(changed[:8])}" + (" ..." if len(changed) > 8 else ""))
        elif not upload_needed:
            log("sync: nada mudou")
        return changed
    raise remote.RemoteError(f"outro PC enviou antes {MAX_ATTEMPTS} vezes seguidas; tento na próxima rodada")


def _mb(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.2f} MB"
