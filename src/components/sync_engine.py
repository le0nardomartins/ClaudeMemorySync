"""
Uma rodada de sincronização, com merge de 3 vias feito NO PC.

  BASE    SHARING_MEMORY/  a versão em que este PC e o destino concordaram da última vez
  LOCAL   NEW_MEMORY/      o estado atual deste PC (já em formato portátil)
  REMOTO  destino          a versão mais nova (server ou repositório Git)

Para cada arquivo, compara local e remoto com a base (merge3.py):
  - mudou só de um lado        -> entra essa mudança, inclusive REMOÇÃO
  - mudou dos dois lados       -> merge por linha/chave; conflito real -> ganha o arquivo
                                  MAIS NOVO (data de modificação) e a versão perdedora
                                  vai para .sync_conflicts/
  - cópia desatualizada        -> um lado "mudou" para um conteúdo mais VELHO que o do
                                  outro: fica o mais novo (nunca volta versão antiga)
Depois envia o resultado dizendo em qual versão se baseou. Se outro PC enviou no meio
do caminho (Conflict), busca de novo e refaz o merge. Só então aplica na pasta do Claude:
arquivo removido vai para .sync_trash/, nunca é apagado de vez.

Arquivo editado DURANTE a rodada nunca é sobrescrito: a gravação confere o arquivo
logo antes e, se ele mudou, deixa como está para o próximo sync mesclar.
O número de versão do destino só serve para ninguém enviar em cima de uma versão velha.
"""

import json
import os
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

# Diferença de data abaixo disso não conta (arredondamento de sistema de arquivos, relógios)
STALE_TOLERANCE = 2.0


def merge_snapshots(base: dict, local: dict, remote_files: dict, contents: Contents):
    """
    Mapas caminho -> meta ({sha256, mtime}). Devolve:
      merged         caminho -> (sha256, mtime) do resultado
      losers         [(caminho, sha256 da versão descartada, de onde ela veio)]
      local_deleted  caminhos que este PC apagou desde o último sync

    O MAIS NOVO SEMPRE GANHA (data de modificação de cada arquivo):
      - só um lado mudou em relação à base -> entra a mudança, A MENOS que ela seja mais
        VELHA que o arquivo do outro lado. Isso é uma cópia desatualizada (um PC que não
        recebeu a versão nova e "mudou" de volta para a antiga): fica a mais nova.
      - os dois mudaram -> merge por linha/chave; trecho em conflito fica com o mais novo.
    Toda versão descartada vai para .sync_conflicts/: nada some em silêncio.
    """
    meta = lambda m, p: m.get(p) or {}
    merged, losers, local_deleted, both = {}, [], [], []
    for rel in sorted(set(base) | set(local) | set(remote_files)):
        b = meta(base, rel).get("sha256")
        l, lt = meta(local, rel).get("sha256"), float(meta(local, rel).get("mtime") or 0)
        r, rt = meta(remote_files, rel).get("sha256"), float(meta(remote_files, rel).get("mtime") or 0)
        if l == r:
            result = (l, max(lt, rt))
        elif l == b:
            # Só o remoto mudou. Conteúdo mais velho que o arquivo daqui não sobrescreve.
            if l is not None and r is not None and rt and rt + STALE_TOLERANCE < lt:
                result = (l, lt)
                losers.append((rel, r, "do outro PC, mais velha"))
            else:
                result = (r, rt)
        elif r == b:
            # Só este PC mudou. Cópia mais velha que a do destino não se espalha.
            if l is None:
                local_deleted.append(rel)
                result = (None, 0)
            elif r is not None and rt and lt + STALE_TOLERANCE < rt:
                result = (r, rt)
                losers.append((rel, l, "deste PC, mais velha"))
            else:
                result = (l, lt)
        else:
            both.append(rel)
            continue
        if result[0] is not None:
            merged[rel] = result

    contents.fetch([(remote_files[p]["sha256"], remote_files[p]["id"]) for p in both
                    if p in remote_files and "id" in remote_files[p]])
    for rel in both:
        b = meta(base, rel).get("sha256")
        l, lt = meta(local, rel).get("sha256"), float(meta(local, rel).get("mtime") or 0)
        r, rt = meta(remote_files, rel).get("sha256"), float(meta(remote_files, rel).get("mtime") or 0)
        local_newer = lt >= rt
        if b is None and l is not None and r is not None:
            # Sem ancestral comum (PC novo, base perdida): juntar linha a linha duplicaria o
            # conteúdo. Fica o arquivo mais novo inteiro; o outro vai para .sync_conflicts/
            data, conflict = (contents.get(l), True) if local_newer else (contents.get(r), True)
        else:
            data, conflict = merge3.merge_file(rel, contents.get(b), contents.get(l), contents.get(r),
                                               sort_json=rel in CONFIG_TEMPLATES, prefer_local=local_newer)
        if data is not None:
            digest = contents.add(data)
            merged[rel] = (digest, lt if digest == l else rt if digest == r else max(lt, rt))
        if conflict:
            losers.append((rel, r, "do outro PC") if local_newer else (rel, l, "deste PC"))
    return merged, losers, local_deleted


# ------------------------------------------------------------------ aplicar na pasta do Claude


def _write(path: Path, data: bytes, mtime: float = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    remote.retry(lambda: tmp.replace(path))
    if mtime:
        # A data do arquivo recebido é a da edição original, não a de agora: senão uma
        # cópia recém-recebida pareceria "mais nova" que a edição real no próximo conflito
        os.utime(path, (mtime, mtime))


def _current_sha(rel: str, path: Path):
    """sha256 do arquivo como ele está AGORA, no formato em que viaja (None se não existe)."""
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    return remote.sha256(sync_mod.portable_bytes(rel, data))


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


def apply_local(merged: dict, local: dict, contents: Contents, stamp: str):
    """
    Grava o resultado na pasta do Claude. Devolve (alterados, pulados).

    Antes de gravar cada arquivo, confere se ele ainda é o que foi lido no começo da
    rodada. Se você editou o arquivo nesse meio tempo, ele NÃO é sobrescrito ("pulados"):
    a edição fica, e o próximo sync a mescla com o que veio do outro PC.
    """
    changed, skipped = [], []
    for rel in sorted(set(merged) | set(local)):
        new, mtime = merged.get(rel, (None, 0))
        old = (local.get(rel) or {}).get("sha256")
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
            if new is None:
                continue
            target = sync_mod.CLAUDE_MD
        else:
            target = sync_mod.SHARING_DIR.joinpath(*PurePosixPath(rel).parts)
        if _current_sha(rel, target) != old:
            skipped.append(rel)
            continue
        if new is None:
            trash = TRASH_DIR / stamp / Path(*PurePosixPath(rel).parts)
            trash.parent.mkdir(parents=True, exist_ok=True)
            remote.retry(lambda: shutil.move(str(target), str(trash)))
            changed.append(f"-{rel}")
        else:
            if rel == sync_mod.CLAUDE_MD_NAME and target.exists():
                shutil.copy2(target, target.with_name("CLAUDE.md.bak-sync"))
            _write(target, contents.get(new), mtime)
            changed.append(rel)
    return changed, skipped


def adjust_base(new_base: dict, local: dict, skipped: list, contents: Contents) -> None:
    """
    Ajusta a base que fica salva para refletir o que de fato está neste PC:
      - arquivo pulado (editado durante a rodada): a base volta a ser a versão lida no
        começo, então o próximo sync vê "os dois mudaram" e mescla a edição, sem perdê-la
      - templates de config: a base vira o template regerado do config real. Diferenças
        que só existem num PC (ex.: caminho do node.exe) não ficam indo e voltando.
    """
    files = new_base["files"]
    if skipped:
        # A base já não é igual à versão do destino: força buscar o manifesto real na próxima
        new_base["version"] = None
    for rel in skipped:
        if rel in local:
            files[rel] = {k: local[rel][k] for k in ("sha256", "size", "mtime")}
        else:
            files.pop(rel, None)
    for name, text in sync_mod.render_templates().items():
        if name not in files:
            continue
        data = text.encode("utf-8")
        digest = contents.add(data)
        if digest != files[name]["sha256"]:
            files[name] = {"sha256": digest, "size": len(data), "mtime": files[name].get("mtime", 0)}


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

        merged, losers, local_deleted = merge_snapshots(base["files"], local, remote_files, contents)
        if len(local_deleted) > mass_delete_limit(len(base["files"])) and not allow_delete:
            raise SyncAborted(f"{len(local_deleted)} arquivos sumiram deste PC desde o último sync "
                              f"(ex.: {', '.join(local_deleted[:3])}). Se foi de propósito, rode "
                              f"python src/main.py --allow-delete; senão, confira as junctions de ~/.claude")

        downloaded = contents.fetch([(m["sha256"], m["id"]) for m in remote_files.values() if "id" in m])
        # Destino vazio (primeiro PC, ou destino recriado): manda tudo, mesmo sem mudança local
        upload_needed = ((remote_version is None and bool(merged))
                         or not same({p: {"sha256": s} for p, (s, _) in merged.items()}, remote_files))
        new_version = remote_version if remote_version is not None else base["version"]

        if upload_needed and upload:
            # Reaproveita ids que o destino já tem (mesmo formato de selagem)
            known = {}
            for m in (remote_m, base):
                if m.get("codec") == codec.name and remote_version is not None:
                    known.update({f["sha256"]: f["id"] for f in m["files"].values() if "id" in f})
            files = {}
            for rel, (digest, mtime) in merged.items():
                data = contents.get(digest)
                files[rel] = {"sha256": digest, "size": len(data), "mtime": mtime,
                              "id": known.get(digest) or codec.blob_id(data)}
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
        new_base = {**new_base, "files": {p: dict(m) for p, m in new_base["files"].items()}}

        stamp = time.strftime("%Y%m%d-%H%M%S")
        for rel, digest, side in losers:
            target = CONFLICTS_DIR / stamp / Path(*PurePosixPath(rel).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(contents.get(digest))
        if losers:
            log(f"conflito em {len(losers)} arquivo(s): ficou a versão mais nova; a outra está em "
                f"{CONFLICTS_DIR / stamp}: " + ", ".join(f"{r} (versão {s})" for r, _, s in losers[:5]))

        changed, skipped = apply_local(merged, local, contents, stamp)
        adjust_base(new_base, local, skipped, contents)
        save_base(new_base, contents)
        if skipped:
            log(f"editado durante o sync, mantido como está (entra na próxima rodada): {', '.join(skipped[:8])}")
        if changed:
            log(f"receber: {len(changed)} mudança(s) aplicada(s) ({_mb(downloaded)} baixados): "
                f"{', '.join(changed[:8])}" + (" ..." if len(changed) > 8 else ""))
        elif not upload_needed and not skipped:
            log("sync: nada mudou")
        return changed
    raise remote.RemoteError(f"outro PC enviou antes {MAX_ATTEMPTS} vezes seguidas; tento na próxima rodada")


def _mb(n: int) -> str:
    return f"{n / 1024:.1f} KB" if n < 1024 * 1024 else f"{n / 1024 / 1024:.2f} MB"
