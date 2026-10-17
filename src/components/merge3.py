"""
Merge de 3 vias: compara cada lado com a BASE (a versão do último sync) para
saber quem mudou o quê, como o Git faz.

  - Só um lado mudou um trecho  -> entra a mudança desse lado (inclusive remoção).
  - Os dois mudaram igual        -> entra uma vez.
  - Os dois só acrescentaram no mesmo ponto -> entram as duas inserções (não é conflito).
  - Os dois mudaram diferente    -> CONFLITO: ganha `local` (este PC é o último a
                                   chegar no server, então é a mudança mais recente
                                   na ordem do server). Quem chama guarda a versão
                                   perdedora para não sumir nada em silêncio.

Texto: por linhas. JSON: por chave (listas por item). Binário: arquivo inteiro.
Ausência do arquivo é representada por None (permite propagar exclusões).
"""

import json
from difflib import SequenceMatcher

MISSING = object()


# ------------------------------------------------------------------ sequências (linhas, itens de lista)


def _hunks(base, other):
    sm = SequenceMatcher(None, base, other, autojunk=False)
    return [(i1, i2, other[j1:j2]) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]


def _overlap(a, b):
    (s1, e1), (s2, e2) = a, b
    if max(s1, s2) < min(e1, e2) or s1 == s2:
        return True
    # Inserção encostada num trecho alterado pelo outro lado também é conflito
    return (s1 == e1 and s2 <= s1 <= e2) or (s2 == e2 and s1 <= s2 <= e1)


def _apply(base, start, end, hunks):
    out, pos = [], start
    for s, e, rep in hunks:
        out += base[pos:s]
        out += rep
        pos = e
    return out + base[pos:end]


def merge3_seq(base, local, remote):
    """Merge de 3 vias de listas. Devolve (resultado, houve_conflito)."""
    events = sorted([(s, e, "l", r) for s, e, r in _hunks(base, local)] +
                    [(s, e, "r", r) for s, e, r in _hunks(base, remote)], key=lambda h: (h[0], h[1]))
    out, pos, conflict, i = [], 0, False, 0
    while i < len(events):
        group = [events[i]]
        gs, ge = events[i][0], events[i][1]
        i += 1
        while i < len(events) and any(_overlap((events[i][0], events[i][1]), (g[0], g[1])) for g in group):
            group.append(events[i])
            ge = max(ge, events[i][1])
            i += 1
        out += base[pos:gs]
        mine = [(s, e, r) for s, e, side, r in group if side == "l"]
        theirs = [(s, e, r) for s, e, side, r in group if side == "r"]
        if mine and theirs:
            a, b = _apply(base, gs, ge, mine), _apply(base, gs, ge, theirs)
            if a == b:
                out += a
            elif all(s == e for s, e, _ in mine + theirs):
                # Os dois só ACRESCENTARAM no mesmo ponto (ex.: regra nova no fim do arquivo):
                # entram as duas, primeiro a que já estava no destino
                out += b + [x for x in a if x not in b]
            else:
                conflict = True
                out += a
        else:
            out += _apply(base, gs, ge, mine or theirs)
        pos = ge
    return out + base[pos:], conflict


# ------------------------------------------------------------------ JSON


def merge3_json(base, local, remote):
    """Merge de 3 vias de valores JSON. MISSING = chave ausente. Devolve (valor, conflito)."""
    if local == remote:
        return local, False
    if local == base:
        return remote, False
    if remote == base:
        return local, False
    if isinstance(local, dict) and isinstance(remote, dict):
        b = base if isinstance(base, dict) else {}
        out, conflict = {}, False
        for key in list(dict.fromkeys([*local, *remote])):
            value, c = merge3_json(b.get(key, MISSING), local.get(key, MISSING), remote.get(key, MISSING))
            conflict |= c
            if value is not MISSING:
                out[key] = value
        return out, conflict
    if isinstance(local, list) and isinstance(remote, list):
        dump = lambda v: json.dumps(v, sort_keys=True, ensure_ascii=False)
        items = {}
        for v in [*(base if isinstance(base, list) else []), *local, *remote]:
            items.setdefault(dump(v), v)
        merged, conflict = merge3_seq([dump(v) for v in (base if isinstance(base, list) else [])],
                                      [dump(v) for v in local], [dump(v) for v in remote])
        return [items[k] for k in merged], conflict
    # Mudou dos dois lados (ou um apagou e o outro mudou): fica o local
    if local is MISSING:
        return remote, False          # alteração vence remoção
    if remote is MISSING:
        return local, False
    return local, True


# ------------------------------------------------------------------ arquivo


def _text(data):
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def merge_file(rel: str, base, local, remote, sort_json: bool = False):
    """
    Bytes (ou None = arquivo ausente) dos três lados -> (bytes | None, conflito).
    Alteração vence remoção: se um lado apagou e o outro mudou, o arquivo fica.
    """
    if local == remote:
        return local, False
    if local == base:
        return remote, False
    if remote == base:
        return local, False
    if local is None:
        return remote, False
    if remote is None:
        return local, False

    texts = [_text(x) if x is not None else "" for x in (base, local, remote)]
    if None in texts:
        return local, True            # binário mudado dos dois lados

    if rel.lower().endswith(".json"):
        try:
            parsed = [json.loads(t) if t.strip() else MISSING for t in texts]
        except json.JSONDecodeError:
            parsed = None
        if parsed and parsed[1] is not MISSING and parsed[2] is not MISSING:
            value, conflict = merge3_json(*parsed)
            out = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=sort_json) + "\n"
            return out.encode("utf-8"), conflict

    lines = [t.splitlines(keepends=True) for t in texts]
    merged, conflict = merge3_seq(*lines)
    for i in range(len(merged) - 1):
        if not merged[i].endswith("\n"):
            merged[i] += "\n"
    return "".join(merged).encode("utf-8"), conflict
