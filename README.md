# interconnection-claude-memory

Mantém a memória do Claude Code igual em todos os seus PCs: skills, agentes, comandos, hooks, `CLAUDE.md`, settings, plugins e MCPs.

Cada PC conversa com um server na Railway. O server junta o que vem de cada um e devolve uma versão mesclada. Nada é substituído nem apagado: cada lado é **complementado** com o que o outro tem.

> **Atenção:** o que é sincronizado inclui as chaves de API do `settings.json` e dos MCPs. Use só entre os seus PCs e nunca publique `NEW_MEMORY/`, `SHARING_MEMORY/` ou o `.env`. O `.gitignore` já os exclui.

## Como funciona

```
 PC (a cada 5 min)                                   Railway
 ─────────────────                                   ───────
 1. RECEBER   GET /latest ◄──────────────────────── versão mais nova (zip pronto)
              → SHARING_MEMORY/
 2. MESCLAR   SHARING_MEMORY → ~/.claude/claude_memory_sharing
              (+ settings.json e mcpServers do .claude.json)
 3. PREPARAR  ~/.claude/claude_memory_sharing → NEW_MEMORY/
 4. ENVIAR    POST /upload ────────────────────────► mescla com a última versão
              (só se houver algo novo)                guarda as 5 últimas no Postgres
```

| Pasta | O que é |
|---|---|
| `~/.claude/claude_memory_sharing/` | A memória de verdade. `~/.claude/skills`, `agents`, `commands`, `hooks`, `output-styles`, `design-references` e `mods` são *junctions* apontando para cá. |
| `SHARING_MEMORY/` | O que foi **recebido** do server: a versão que junta todos os PCs. |
| `NEW_MEMORY/` | O que este PC **envia**: espelho da pasta do Claude, com os caminhos do usuário trocados para o formato portátil. |

O passo 2 só roda quando **todos** os arquivos da versão recebida chegaram com o hash certo. Um download pela metade nunca é aplicado.

### Regras do merge

- **Texto:** linhas que só existem de um lado entram; nada é apagado.
- **Mesmo trecho alterado nos dois PCs:** ganha o arquivo editado por último (data de modificação).
- **JSON:** chaves novas entram, listas são somadas sem duplicar e, em valor divergente, ganha o mais novo.
- **Binário:** ganha o arquivo mais novo inteiro.
- **`settings.template.json` / `mcp.template.json`:** mesclados direto no `~/.claude/settings.json` e nos `mcpServers` do `~/.claude.json`, com backup `.bak-sync`.
- **Caminhos:** `C:\Users\<você>` vira `~` nos `.md` e um destes tokens nos templates JSON: `{{HOME}}`, `{{HOME_FWD}}`, `{{HOME_POSIX}}` ou `{{HOME_ESC}}`. No outro PC, cada um é trocado pelo caminho do usuário de lá.

## Instalação neste PC

Requisitos: Python 3.10+ e `pip install -r requirements.txt`.

1. **Gere o par de chaves deste PC:**
   ```
   python src/components/remote_client.py keygen pc-<nome-dificil-de-adivinhar>
   ```
   As linhas `CLIENT_ID` e `CLIENT_PRIVATE_KEY` vão para o `.env`. A linha `pc-...:<chave>` vai para a variável `CLIENT_PUBLIC_KEYS` do server na Railway. Separe os PCs com vírgula.

2. **Monte o `.env`** na raiz do projeto:
   ```
   SERVER_URL=https://<seu-app>.up.railway.app
   CLIENT_ID=pc-...
   CLIENT_PRIVATE_KEY=...
   ```

3. **Rode uma vez para conferir:**
   ```
   python src/main.py
   ```

4. **Deixe rodando sozinho a cada 5 minutos**, iniciando com o Windows:
   ```
   powershell -ExecutionPolicy Bypass -File install_autostart.ps1
   ```
   O log fica em `sync.log`. Para remover, rode o mesmo comando com `-Uninstall` no final.

Para um PC novo, siga o [SETUP_OUTRO_PC.md](SETUP_OUTRO_PC.md). Ele tem um prompt pronto para o Claude daquele PC fazer a configuração.

## Comandos

| Comando | O que faz |
|---|---|
| `python src/main.py` | Uma rodada completa: receber → mesclar → preparar → enviar. |
| `python src/main.py --loop` | Roda para sempre, a cada 5 minutos (`--interval <segundos>` para mudar). |
| `python src/main.py --no-upload` | Só recebe e mescla. |
| `python src/main.py --offline` | Só prepara a `NEW_MEMORY`, sem falar com o server. |
| `python src/components/merge_sharing.py --watch` | Fica esperando a `SHARING_MEMORY` ficar completa e aplica sozinho. |
| `python src/components/remote_client.py keygen <pc>` | Gera as chaves de um PC. |

Só uma sincronização roda por vez em cada PC. Uma segunda execução encerra sozinha.

## Segurança

- **Autenticação Ed25519:** a chave privada fica só no `.env` de cada PC e nunca é enviada. A Railway guarda apenas as chaves públicas, que servem para verificar e não para assinar. Vazar as variáveis da Railway não permite se passar por nenhum PC.
- **Assinatura de cada requisição:** cobre o método, a rota, o horário e o hash do corpo. O server recusa assinatura reenviada, corpo alterado e pedido com mais de 5 minutos.
- **Server:** todas as rotas exigem assinatura. Quem erra 10 vezes tem o IP bloqueado por 15 minutos. O server também tem limite de tamanho e de requisições simultâneas e proteção contra zip bomb e zip slip.
- **Revogar um PC:** apague a linha dele em `CLIENT_PUBLIC_KEYS` na Railway.

## Limitações

- **Apagar não se propaga.** O que você apagar em um PC volta no próximo sync. Para remover de vez, apague em todos.
- **Relógio:** os PCs precisam estar com o horário certo, porque o "mais novo" de um conflito é decidido pela data dos arquivos.
- **Plugins e MCPs:** a configuração chega, mas o programa em si não. Plugins e MCPs que dependem de um executável precisam ser instalados em cada PC, e os MCPs com login precisam ser autenticados em cada um. A seção *CONNECTION BETWEEN PCs* do `CLAUDE.md` instrui o Claude a verificar e instalar essas pendências sozinho.
- **Só são sincronizados os MCPs globais.** MCPs de projeto (`.mcp.json` ou dentro de um projeto no `.claude.json`) ficam de fora.
- **Pastas `.git`** dentro da memória não são sincronizadas.
- **Histórico:** o server guarda só as 5 últimas versões.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `HTTP 401` | A chave pública deste PC não está em `CLIENT_PUBLIC_KEYS`, o `CLIENT_ID` não bate com o nome usado lá, ou o relógio do PC está errado. |
| `HTTP 429` | Muitas falhas de autenticação a partir deste IP. Corrija o `.env` e espere 15 minutos. |
| `SERVER_URL não definida` | Falta o `SERVER_URL` no `.env`. A rodada só prepara a `NEW_MEMORY`. |
| `já existe uma sincronização rodando` | O loop automático já está rodando. É normal. |
| Mudança de outro PC não chegou | O outro PC ainda não enviou. Espere a próxima rodada dele e depois a deste. |

## Estrutura

```
src/
  main.py                          rodada completa e modo --loop
  components/
    sync_claude_templates.py       gera os templates do settings/MCP (caminhos → tokens)
    merge_sharing.py               aplica SHARING_MEMORY na pasta do Claude
    remote_client.py               zip + manifesto, requisições assinadas, keygen
install_autostart.ps1              tarefa agendada do Windows (a cada 5 min)
SETUP_OUTRO_PC.md                  prompt para configurar outro PC
requirements.txt
```

O server (Node + Express + Postgres) roda na Railway. O código dele não está neste repositório.
