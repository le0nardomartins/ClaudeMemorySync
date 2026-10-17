# Arquitetura do cliente

- [A rodada](#a-rodada)
- [Pastas e arquivos](#pastas-e-arquivos)
- [Caminhos portáteis](#caminhos-portáteis)
- [Protocolo com o server](#protocolo-com-o-server)
- [Merge](#merge)
- [Estado local](#estado-local)
- [Módulos](#módulos)

## A rodada

`src/main.py` executa uma rodada, ou uma por intervalo com `--loop`:

```
receive()        GET /latest?since=<última versão recebida>
                   204 → nada novo
                   200 → manifesto da versão nova
                         materialize(): monta SHARING_MEMORY/ reaproveitando o que já existe
                         localmente e baixa (POST /blobs) só o que falta
                 merge_sharing.apply()  se a versão recebida ainda não foi aplicada
refresh_local()  sync_claude_templates.sync()  junctions + templates de settings/MCP
                 copy_sharing()  ~/.claude/claude_memory_sharing → NEW_MEMORY/
send()           scan(NEW_MEMORY) → fingerprint
                   igual ao recebido ou ao último enviado → não envia
                   diferente → POST /upload com manifesto + conteúdo novo
```

Receber vem **antes** de enviar: assim o PC só manda o que é realmente dele, e o server faz menos merges.

Só uma rodada roda por vez (`.sync.lock`, com lock de arquivo do sistema operacional).

## Pastas e arquivos

| Caminho | Quem escreve | Conteúdo |
|---|---|---|
| `~/.claude/claude_memory_sharing/` | Você, o Claude e o merge | Memória portátil: as pastas de `PORTABLE_DIRS` e os templates gerados. |
| `~/.claude/<pasta>` | `ensure_structure()` | Junction para `claude_memory_sharing/<pasta>`. |
| `SHARING_MEMORY/` | `materialize()` | Última versão recebida, completa, com `manifest.json`. |
| `NEW_MEMORY/` | `copy_sharing()` | Espelho do sharing + `CLAUDE.md`, com caminhos portáteis e sem as exclusões. |
| `.sync_state.json` | `merge_sharing.save_state()` | Versões e fingerprints (abaixo). |
| `sync.log` | `main.py` (via `pythonw`) | Log da tarefa agendada. Rotaciona em 2 MB. |

Ficam de fora de `NEW_MEMORY` (`remote_client.is_excluded`): `node_modules/`, `.git/`, `__pycache__/` e `skills/synced/`. O server aplica a mesma regra e descarta esses caminhos mesmo se um cliente antigo os enviar.

Para compartilhar uma pasta nova de `~/.claude`, acrescente o nome em `PORTABLE_DIRS` (`sync_claude_templates.py`).

## Caminhos portáteis

O mesmo arquivo precisa funcionar em `C:\Users\ana` e em `C:\Users\bruno`.

| Onde | Conversão no envio | No recebimento |
|---|---|---|
| `.md` | `C:\Users\ana` → `~` (ou `$HOME` logo depois de aspas) | Comparado ignorando essa diferença: uma linha só com o caminho diferente não conta como mudança. |
| `settings.template.json`, `mcp.template.json` | `{{HOME}}`, `{{HOME_FWD}}` (`C:/Users/ana`), `{{HOME_POSIX}}` (`/c/Users/ana`), `{{HOME_ESC}}` (`C:\\Users\\ana`) | `expand_home()` troca pelo caminho deste PC antes de mesclar no `settings.json` e no `~/.claude.json`. |
| Hooks com `C:\Program Files\nodejs\node.exe` | Viram `node` | Resolvido pelo PATH. |

## Protocolo com o server

Todas as requisições são assinadas com Ed25519. A mensagem assinada é:

```
MÉTODO\nCAMINHO\nTIMESTAMP_MS\nSHA256_HEX(corpo)
```

e vai nos cabeçalhos `X-Client-Id`, `X-Timestamp` e `X-Signature` (base64).

**Formato "bundle"** (corpo binário de `/upload`, `/latest` e `/blobs`): uma sequência de registros.

```
[uint32 BE: tamanho do nome][nome UTF-8][uint32 BE: tamanho dos dados][dados deflate-raw]
```

O nome é `manifest.json` ou o sha256 (hex) do conteúdo **descompactado**. O manifesto tem este formato:

```json
{ "version": 21,
  "files": { "skills/x/SKILL.md": { "sha256": "…", "size": 1234, "mtime": 1760700000.5 } } }
```

| Rota | Pedido | Resposta |
|---|---|---|
| `GET /latest?since=N` | — | `204` se a versão mais nova é `N`. Senão, bundle só com `manifest.json` e o cabeçalho `X-Version-Id`. |
| `POST /blobs` | JSON `{"hashes": [...]}` (o cliente manda em lotes de 500) | Bundle com os conteúdos que o server tem. |
| `POST /upload` | Bundle: `manifest.json` primeiro, depois só os conteúdos que o server não tem | `201 {versionId, fileCount, totalBytes, newBytes, unchanged}`, ou `409 {missing: [...]}` |

**Como o cliente decide o que enviar.** Ele presume que o server já tem tudo o que estava na última versão recebida (`SHARING_MEMORY/manifest.json`) e manda o resto. Se o server responder `409` com hashes que faltam (por exemplo, apagados na limpeza de versões antigas), o cliente reenvia uma vez incluindo esses conteúdos.

**Como o cliente monta o que recebeu.** Para cada hash do manifesto, procura nesta ordem:
1. o arquivo correspondente em `SHARING_MEMORY` (versão anterior);
2. um arquivo com o mesmo hash em `NEW_MEMORY`;
3. o server.

Todo conteúdo lido de disco é conferido pelo hash antes de ser usado.

## Merge

O merge do PC (`merge_sharing.py`) e o do server (`merge.js`) seguem as mesmas regras. **Mudou um, mude o outro.**

| Tipo | Regra |
|---|---|
| Arquivo só de um lado | É mantido. |
| Texto | Diff por linhas: o que só existe de um lado entra. Trecho alterado nos dois lados → fica a versão do arquivo com `mtime` maior. |
| JSON | Merge recursivo: chaves somam, listas somam sem duplicar, valor divergente → o mais novo. JSON inválido cai para a regra de texto. |
| Binário (não UTF-8) | O mais novo inteiro. |
| `settings.template.json` | Expandido e mesclado direto em `~/.claude/settings.json` (backup `.bak-sync`). |
| `mcp.template.json` | Expandido e mesclado só na chave `mcpServers` de `~/.claude.json`. |
| `CLAUDE.md` | Substitui `~/.claude/CLAUDE.md` inteiro se for mais novo (backup `CLAUDE.md.bak-sync`). |

O merge no PC só roda quando **todos** os arquivos de `SHARING_MEMORY` conferem com o manifesto, e cada versão é aplicada uma única vez (`applied_version`).

## Estado local

`.sync_state.json`:

| Chave | Uso |
|---|---|
| `pulled_version` | Vai em `GET /latest?since=` para o server responder `204` quando nada mudou. |
| `pulled_fingerprint` | Hash do conteúdo recebido. Se a `NEW_MEMORY` é igual a ele, não há o que enviar. |
| `uploaded_fingerprint` | Hash do último envio, para não reenviar a mesma coisa. |
| `applied_version` | Última versão mesclada na pasta do Claude. |

O fingerprint é o sha256 da lista ordenada `caminho + sha256(conteúdo)`. Ele não muda quando só a data de um arquivo muda.

Apagar `.sync_state.json` é seguro: a próxima rodada baixa o manifesto de novo (reaproveitando o conteúdo local) e reaplica o merge, que é idempotente.

## Módulos

| Arquivo | Responsabilidade |
|---|---|
| `src/main.py` | Orquestra a rodada, o loop, o lock e o log. |
| `src/components/sync_claude_templates.py` | `PORTABLE_DIRS`, junctions, geração dos templates, tokens `{{HOME*}}`. |
| `src/components/remote_client.py` | Exclusões, bundle, `scan`, `materialize`, cliente HTTP assinado, `keygen`. |
| `src/components/merge_sharing.py` | Merge de texto/JSON/binário, aplicação na pasta do Claude e nos configs, estado. |
