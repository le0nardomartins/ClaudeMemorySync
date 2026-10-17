# Arquitetura do cliente

- [A rodada](#a-rodada)
- [Pastas e arquivos](#pastas-e-arquivos)
- [Caminhos portáteis](#caminhos-portáteis)
- [Merge de 3 vias](#merge-de-3-vias)
- [Remoções](#remoções)
- [Ordem e concorrência](#ordem-e-concorrência)
- [Criptografia](#criptografia)
- [Destinos](#destinos)
- [Protocolo com o server](#protocolo-com-o-server)
- [Módulos](#módulos)

## A rodada

`src/main.py` executa uma rodada, ou uma por intervalo com `--loop`:

```
refresh_local()    sync_claude_templates.sync()   links + templates de settings/MCP
                   copy_sharing()                  ~/.claude/claude_memory_sharing → NEW_MEMORY/ (LOCAL)
sync_round()       (components/sync_engine.py)
  1. base   = SHARING_MEMORY/manifest.json        a versão do último sync
  2. remoto = destino.latest(since=base.version)  nada novo? remoto = base
  3. merge  = merge_snapshots(base, local, remoto)
  4. trava  : remoções locais demais → para e pede --allow-delete
  5. envia  : se o resultado difere do remoto → destino.put(base=remoto.version, ...)
              Conflict (outro PC chegou antes) → volta ao passo 2 (até 5 vezes)
  6. aplica : resultado → pasta do Claude, settings.json, ~/.claude.json, CLAUDE.md
  7. base   = resultado (ou o remoto, com --no-upload) → SHARING_MEMORY/
```

A aplicação na pasta do Claude só acontece depois do envio aceito. Se o processo cair no meio, a rodada seguinte refaz tudo a partir da mesma base. O resultado é o mesmo, porque o merge é determinístico.

Só uma rodada roda por vez (`.sync.lock`, com lock de arquivo do sistema operacional).

## Pastas e arquivos

| Caminho | Quem escreve | Conteúdo |
|---|---|---|
| `~/.claude/claude_memory_sharing/` | Você, o Claude e a rodada | Memória portátil: as pastas de `PORTABLE_DIRS` e os templates gerados. |
| `~/.claude/<pasta>` | `ensure_structure()` | Junction (Windows) ou symlink para `claude_memory_sharing/<pasta>`. |
| `NEW_MEMORY/` | `copy_sharing()` | LOCAL: espelho do sharing + `CLAUDE.md`, com caminhos portáteis e sem as exclusões. |
| `SHARING_MEMORY/` | `save_base()` | BASE: conteúdo completo da última versão acordada + `manifest.json`. |
| `.sync_conflicts/<data>/` | `sync_round()` | Versão perdedora de cada conflito. |
| `.sync_trash/<data>/` | `apply_local()` | Arquivos removidos por outro PC. |
| `.sync_repo/` | `GitStore` | Clone do repositório de dados (só modo Git). É só um cache. |
| `sync.log` | `main.py` | Log do autostart. Rotaciona em 2 MB. |

Ficam de fora (`remote_client.is_excluded`): `node_modules/`, `.git/`, `__pycache__/` e `skills/synced/`.

Para compartilhar uma pasta nova de `~/.claude`, acrescente o nome em `PORTABLE_DIRS` (`sync_claude_templates.py`).

Apagar `SHARING_MEMORY/` é seguro. Sem base, o próximo merge trata tudo como "acrescentado dos dois lados": nada é removido e só há conflito onde os dois PCs têm versões diferentes do mesmo trecho.

## Caminhos portáteis

O mesmo arquivo precisa funcionar em `C:\Users\ana` e em `C:\Users\bruno`.

| Onde | Na NEW_MEMORY (o que viaja) | Na volta |
|---|---|---|
| `.md` | `C:\Users\ana` → `~` (ou `$HOME` logo depois de aspas) | O arquivo mesclado é gravado assim, com `~`. |
| `settings.template.json`, `mcp.template.json` | `{{HOME}}`, `{{HOME_FWD}}` (`C:/Users/ana`), `{{HOME_POSIX}}` (`/c/Users/ana`), `{{HOME_ESC}}` (`C:\\Users\\ana`). Chaves ordenadas, para o texto ser estável. | `expand_home()` troca pelo caminho deste PC. |
| Hooks com `C:\Program Files\nodejs\node.exe` | Viram `node` | Resolvido pelo PATH. |

Os templates não são gravados de volta como arquivo. A rodada calcula a diferença entre o template local e o mesclado e aplica **só essa diferença** no `settings.json` e nos `mcpServers` do `~/.claude.json` reais, com merge de 3 vias por chave. Assim, o que só existe neste PC (o caminho completo do `node.exe`, contadores do `~/.claude.json`) fica intacto.

## Merge de 3 vias

`merge3.py`, por arquivo, com base B, local L e remoto R (`None` = arquivo ausente):

| Caso | Resultado |
|---|---|
| L = R | L |
| L = B | R (só o remoto mudou, inclusive remoção) |
| R = B | L (só este PC mudou, inclusive remoção) |
| L ausente, R mudou / R ausente, L mudou | Fica quem mudou (edição vence remoção) |
| Texto mudado dos dois lados | Merge por linhas (abaixo) |
| JSON mudado dos dois lados | Merge recursivo por chave; listas por item, como linhas |
| Binário mudado dos dois lados | Conflito: fica L |

**Texto.** Calcula os trechos alterados de cada lado em relação à base (`difflib`) e junta os que se sobrepõem:
- trecho alterado só por um lado → aplica;
- os dois deram o mesmo resultado → aplica uma vez;
- os dois **só inseriram** no mesmo ponto → entram as duas inserções, primeiro a remota (o caso comum de acrescentar regras no fim do arquivo);
- senão → **conflito**: fica a versão local, e a remota inteira vai para `.sync_conflicts/`.

## Remoções

Com a base, "não existe aqui" passa a ter dois sentidos diferentes:
- **existia na base e sumiu** → foi removido, e a remoção se propaga;
- **não existia na base** → é novo do outro lado, e entra aqui.

Proteções:
- **Trava de remoção em massa** (`mass_delete_limit`): se este PC removeu mais de `max(10, 20% da base)` arquivos desde o último sync, a rodada para com `SyncAborted` e pede `--allow-delete`. Isso evita que uma junction quebrada ou uma pasta vazia apague tudo nos outros PCs.
- **Lixeira:** arquivo removido por outro PC é movido para `.sync_trash/<data>/`, nunca apagado.
- **Edição vence remoção:** se um PC apagou e o outro editou, o arquivo fica.

## Ordem e concorrência

Não há relógio de PC na decisão. A ordem é a **sequência de versões no destino**:
- todo envio diz `base` = a versão remota em que o merge se baseou, e `version = base + 1`;
- o destino só aceita se `base` ainda for a versão mais nova (server: `409 conflict`; Git: push recusado). Senão, o PC busca a versão nova e refaz o merge;
- num conflito real, ganha o PC que enviou depois, porque a mudança dele é a mais recente na ordem do destino.

## Criptografia

`crypto_codec.py`, ativada por `SYNC_KEY` (32 bytes aleatórios em base64, igual em todos os PCs):

| Item | Sem `SYNC_KEY` | Com `SYNC_KEY` |
|---|---|---|
| Conteúdo e manifesto | `P1` + deflate | `E1` + nonce (12 bytes) + AES-256-GCM(deflate), com o id do blob (ou `"manifest"`) como dado autenticado |
| Id do blob | sha256 do conteúdo | HMAC-SHA256 do conteúdo |
| O destino vê | Conteúdo legível | Só bytes aleatórios, ids opacos e tamanhos |

As chaves de cifra e de id saem da `SYNC_KEY` por HKDF-SHA256 (com `info` diferente), então uma não revela a outra.

O manifesto (cifrado) registra o `codec` usado. Se um PC passa a usar `SYNC_KEY`, ele não reaproveita blobs de versões sem cifra e reenvia tudo cifrado. O destino apaga os antigos quando eles saem das 3 últimas versões.

O manifesto, dentro do envelope:

```json
{ "format": 2, "version": 21, "codec": "aes-256-gcm",
  "files": { "skills/x/SKILL.md": { "sha256": "…", "size": 1234, "id": "…" } } }
```

## Destinos

`main.py` escolhe pelo `.env`:

| `.env` | Classe | Onde ficam as versões |
|---|---|---|
| `SERVER_URL=...` (padrão) | `remote_client.Client` | Server HTTP (protocolo abaixo) |
| `SYNC_BACKEND=git` + `GIT_REPO_URL=...` | `git_backend.GitStore` | Repositório Git privado ([git-backend.md](git-backend.md)) |

As duas classes têm a mesma interface:

| Método | O que faz |
|---|---|
| `latest(since)` | `(versão mais nova ou None, manifesto selado, ou None se for a própria since)` |
| `get_blobs(ids)` | `{id: blob selado}` |
| `put(base, version, manifest, refs, blobs)` | Grava a versão. Lança `Conflict` ou `MissingOnServer`. |

## Protocolo com o server

Todas as requisições são assinadas com Ed25519: `MÉTODO\nCAMINHO\nTIMESTAMP_MS\nSHA256_HEX(corpo)` nos cabeçalhos `X-Client-Id`, `X-Timestamp` e `X-Signature`.

Formato **bundle**: `[uint32 BE tamanho do nome][nome][uint32 BE tamanho][dados]` repetido.

| Rota | Pedido | Resposta |
|---|---|---|
| `GET /latest?since=N` | — | `204` se a mais nova é `N` ou não há nenhuma; senão bundle `[manifest]`. `X-Version-Id` sempre (vazio = destino vazio). |
| `POST /blobs` | JSON `{"ids": [...]}` (lotes de 500) | Bundle com os blobs encontrados. |
| `POST /upload` | Bundle `[meta.json {base, version, refs}, manifest, <id>...]` só com blobs novos | `201`, `409 {conflict: true, latest}` ou `409 {missing: [...]}` |

O server não lê manifesto nem blobs. Ele usa só `refs` para saber o que cada versão usa e apagar o que nenhuma usa. Detalhes no repositório do server (`docs/api.md`).

## Módulos

| Arquivo | Responsabilidade |
|---|---|
| `src/main.py` | Orquestra a rodada, o loop, o lock, o log e a escolha do destino. |
| `src/components/sync_claude_templates.py` | `PORTABLE_DIRS`, links, geração dos templates, tokens `{{HOME*}}`. |
| `src/components/sync_engine.py` | Base, merge dos snapshots, trava, envio com nova tentativa, aplicação, lixeira, conflitos. |
| `src/components/merge3.py` | Merge de 3 vias de texto, JSON e binário. |
| `src/components/crypto_codec.py` | `SYNC_KEY`, selar/abrir, ids. |
| `src/components/remote_client.py` | Destino server, bundle, exclusões, `scan`, `keygen`, `synckey`. |
| `src/components/git_backend.py` | Destino repositório Git. |
