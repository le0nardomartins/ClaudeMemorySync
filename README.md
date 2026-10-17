# Claude Memory Sync

Mantém a configuração do **Claude Code** igual em todos os seus computadores: skills, agentes, slash commands, hooks, output styles, o `CLAUDE.md` global, o `settings.json`, os plugins ativos e os servidores MCP.

Você trabalha normalmente em qualquer PC. A cada 15 minutos cada PC envia o que mudou para um server próprio, recebe o que os outros PCs mudaram e **complementa** a sua configuração. Nada é substituído às cegas nem apagado.

> **Este repositório é o cliente** (Python, roda em cada PC). O server fica em [backend-memory-interconnection-claude](https://github.com/le0nardomartins/backend-memory-interconnection-claude) (Node + Postgres, feito para a Railway).

> [!WARNING]
> A sincronização inclui as **chaves de API** do `settings.json` e dos MCPs. Use só entre computadores seus e nunca publique `NEW_MEMORY/`, `SHARING_MEMORY/` ou o `.env`. O `.gitignore` já exclui essas pastas.

---

## Sumário

- [Por que existe](#por-que-existe)
- [Como funciona](#como-funciona)
- [Requisitos](#requisitos)
- [Início rápido](#início-rápido)
- [Adicionar outro PC](#adicionar-outro-pc)
- [O que é sincronizado](#o-que-é-sincronizado)
- [Como os conflitos são resolvidos](#como-os-conflitos-são-resolvidos)
- [Comandos](#comandos)
- [Segurança](#segurança)
- [Limitações](#limitações)
- [Problemas comuns](#problemas-comuns)
- [Estrutura do projeto](#estrutura-do-projeto)
- [Documentação completa e skill para o Claude](#documentação-completa-e-skill-para-o-claude)

---

## Por que existe

O Claude Code guarda tudo em `~/.claude` e `~/.claude.json`, e cada computador tem a própria cópia. Criou uma skill no notebook? Ela não existe no desktop. Configurou um MCP no trabalho? Precisa refazer em casa.

Copiar a pasta inteira não resolve: ela tem caminhos absolutos (`C:\Users\fulano\...`), dados de sessão, logins e arquivos que só fazem sentido em uma máquina. Este projeto sincroniza **só o que é portátil**, troca os caminhos por tokens neutros e **mescla** as mudanças de vários PCs em vez de sobrescrever uma com a outra.

## Como funciona

```
 Cada PC (a cada 15 min)                              Server (Railway)
 ───────────────────────                              ────────────────
 1. RECEBER   GET /latest  ◄────────────────────────  manifesto da versão mais nova
              POST /blobs  ◄────────────────────────  só o conteúdo que falta neste PC
              → SHARING_MEMORY/

 2. MESCLAR   SHARING_MEMORY → ~/.claude/claude_memory_sharing
              (+ settings.json e mcpServers do ~/.claude.json)

 3. PREPARAR  ~/.claude/claude_memory_sharing → NEW_MEMORY/
              (caminhos do usuário viram ~ e {{HOME}})

 4. ENVIAR    POST /upload ────────────────────────►  mescla com a última versão
              manifesto + só o conteúdo novo           guarda as 3 últimas no Postgres
              (só se houver algo novo)                 (cada conteúdo uma vez só, por sha256)
```

O que circula entre PC e server é um **manifesto** (caminho → sha256, tamanho e data de cada arquivo) e o conteúdo que o outro lado ainda não tem. Alterar uma linha de uma skill gera um envio de poucos KB, e não um reenvio da pasta inteira.

As três pastas envolvidas:

| Pasta | Papel |
|---|---|
| `~/.claude/claude_memory_sharing/` | A memória de verdade. `~/.claude/skills`, `agents`, `commands`, `hooks`, `output-styles`, `design-references` e `mods` passam a ser *junctions* (links de pasta) apontando para cá. |
| `SHARING_MEMORY/` | O que foi **recebido** do server: a versão que junta todos os PCs. |
| `NEW_MEMORY/` | O que este PC **envia**: espelho da pasta acima, com os caminhos trocados para o formato portátil. |

O passo 2 só roda quando **todos** os arquivos recebidos conferem com o hash do manifesto. Um download pela metade nunca é aplicado.

Detalhes em [docs/architecture.md](docs/architecture.md).

## Requisitos

| | |
|---|---|
| Sistema | Windows 10/11 (feito e testado nele). Em macOS/Linux o cliente usa symlinks no lugar de junctions, mas o agendamento automático precisa ser montado à mão (cron, launchd ou systemd). |
| Python | 3.10 ou mais novo, com `pip install -r requirements.txt` (só `cryptography`). |
| Server | Uma instância do [server](https://github.com/le0nardomartins/backend-memory-interconnection-claude) no ar. O plano da Railway com Postgres é o caminho documentado. |
| Claude Code | Instalado em cada PC, com `~/.claude` já criado. |

## Início rápido

**1. Suba o server.** Siga o README do [backend](https://github.com/le0nardomartins/backend-memory-interconnection-claude). Ao final você terá uma URL como `https://seu-app.up.railway.app`.

**2. Clone este repositório e instale a dependência:**

```powershell
git clone https://github.com/le0nardomartins/memory-interconnection-claude.git
cd memory-interconnection-claude
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

**3. Gere o par de chaves deste PC:**

```powershell
.venv\Scripts\python src\components\remote_client.py keygen pc-notebook-x7k2
```

A saída tem duas partes:
- `CLIENT_ID` e `CLIENT_PRIVATE_KEY`: vão para o `.env` deste PC. São privadas.
- `pc-notebook-x7k2:<base64>`: vai para a variável `CLIENT_PUBLIC_KEYS` do server. Separe os PCs com vírgula. Essa linha pode ser pública.

Use um nome difícil de adivinhar: ele também identifica o PC nos logs.

**4. Crie o `.env`** na raiz do projeto:

```ini
SERVER_URL=https://seu-app.up.railway.app
CLIENT_ID=pc-notebook-x7k2
CLIENT_PRIVATE_KEY=...
```

**5. Rode uma vez e confira a saída:**

```powershell
.venv\Scripts\python src\main.py
```

Na primeira execução o sync faz três coisas:
- cria `~/.claude/claude_memory_sharing`;
- move `skills`, `agents` e as outras pastas portáteis para lá e cria as junctions no lugar delas;
- gera os templates de settings e MCP e envia tudo ao server.

**6. Deixe rodando sozinho**, iniciando com o Windows:

```powershell
powershell -ExecutionPolicy Bypass -File install_autostart.ps1
```

Isso cria a tarefa agendada `ClaudeMemorySync`, que roda escondida (`pythonw`) e grava o log em `sync.log`. Para remover, rode o mesmo comando com `-Uninstall`.

## Adicionar outro PC

Repita os passos 2 a 6 no outro computador, com um nome de PC diferente, e acrescente a chave pública dele em `CLIENT_PUBLIC_KEYS` no server.

Há um jeito mais simples: abra o Claude Code no outro PC e peça para ele usar a skill deste projeto (veja abaixo). Ela conduz a instalação, os backups e a verificação dos MCPs e plugins que vieram do primeiro PC. O passo a passo manual está em [docs/setup.md](docs/setup.md).

## O que é sincronizado

| Sincronizado | Não sincronizado |
|---|---|
| `skills/`, `agents/`, `commands/`, `hooks/`, `output-styles/`, `design-references/`, `mods/` | `node_modules/`: reinstale com `npm ci` (o log avisa quais skills precisam) |
| `~/.claude/CLAUDE.md` global | `skills/synced/`: gerenciada pelo app Claude Desktop em cada PC |
| `~/.claude/settings.json`: permissões, hooks, env, plugins ativos | `.git/` e `__pycache__/` |
| `mcpServers` do `~/.claude.json` (só os globais) | Login do Claude (`.credentials.json`), histórico e sessões |
| Comandos para reinstalar os plugins (`plugins-install.txt`) | MCPs de projeto (`.mcp.json`) |

A sincronização leva a **configuração** de MCPs e plugins, mas não o programa. Se um MCP aponta para um executável que só existe no outro PC, ele precisa ser instalado neste. A skill do projeto faz essa verificação.

## Como os conflitos são resolvidos

O merge é **complementar**: cada lado ganha o que o outro tem, e nada é apagado.

- **Texto:** linhas que só existem de um lado entram. Se o mesmo trecho foi alterado nos dois PCs, ganha o arquivo editado por último.
- **JSON:** chaves novas entram, listas são somadas sem duplicar e, em valor divergente, ganha o mais novo.
- **Binário:** ganha o arquivo mais novo inteiro.
- **`settings.json` e `~/.claude.json`:** mesclados direto no arquivo real, com backup `.bak-sync`.
- **`CLAUDE.md` global:** substituído inteiro se o recebido for mais novo, com backup `CLAUDE.md.bak-sync`.
- **Caminhos:** `C:\Users\<você>` vira `~` nos `.md`. Nos JSON, vira um destes tokens: `{{HOME}}`, `{{HOME_FWD}}`, `{{HOME_POSIX}}` ou `{{HOME_ESC}}`. Cada PC troca os tokens pelo próprio caminho.

A mesma lógica roda no PC (`merge_sharing.py`) e no server (`merge.js`).

## Comandos

| Comando | O que faz |
|---|---|
| `python src/main.py` | Uma rodada completa: receber → mesclar → preparar → enviar. |
| `python src/main.py --loop` | Roda para sempre, a cada 15 minutos (`--interval <segundos>` para mudar). |
| `python src/main.py --no-upload` | Só recebe e mescla. |
| `python src/main.py --offline` | Só prepara a `NEW_MEMORY`, sem falar com o server. |
| `python src/components/merge_sharing.py --watch` | Espera a `SHARING_MEMORY` ficar completa e aplica sozinho. |
| `python src/components/remote_client.py keygen <pc>` | Gera as chaves de um PC. |
| `install_autostart.ps1 [-Interval 900] [-Uninstall]` | Instala ou remove a tarefa agendada do Windows. |
| `restart_sync.bat` | Reinicia a tarefa agendada (use depois de um `git pull`). |

Só uma sincronização roda por vez em cada PC (`.sync.lock`). Uma segunda execução encerra sozinha.

## Segurança

- **Sem senha compartilhada.** Cada PC tem um par de chaves Ed25519. A chave privada fica só no `.env` do PC, e o server guarda apenas as públicas. Vazar as variáveis do server não permite se passar por nenhum PC.
- **Toda requisição é assinada.** A assinatura cobre o método, a rota, o horário e o hash do corpo. O server recusa assinatura reenviada, corpo alterado e pedido com mais de 5 minutos.
- **HTTPS obrigatório.** O cliente recusa `http://`, exceto para `localhost`.
- **Integridade.** Cada arquivo é conferido pelo sha256 nas duas pontas, e caminhos como `../` são recusados.
- **Revogar um PC:** apague a linha dele em `CLIENT_PUBLIC_KEYS` no server.

## Limitações

- **Apagar não se propaga.** Um arquivo apagado volta no próximo sync, porque os outros PCs e o próprio server ainda o têm. Remover de vez exige apagar em todos os PCs e zerar o histórico do server ([como fazer](docs/troubleshooting.md#situações)).
- **Relógio.** O "mais novo" de um conflito é decidido pela data dos arquivos, então os PCs precisam estar com o horário certo.
- **Histórico curto.** O server guarda só as 3 últimas versões. Não é um backup.
- **Programas não viajam.** MCPs com executável, plugins e logins (OAuth do Figma, por exemplo) precisam ser instalados ou autenticados em cada PC.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `HTTP 401` | A chave pública deste PC não está em `CLIENT_PUBLIC_KEYS`, o `CLIENT_ID` não bate com o nome usado lá ou o relógio do PC está errado. |
| `HTTP 415` / `manifesto inválido` | Cliente e server em versões diferentes. Atualize os dois (`git pull`) e rode `restart_sync.bat`. |
| `HTTP 429` | Muitas falhas de autenticação a partir deste IP. Corrija o `.env` e espere 15 minutos. |
| `SERVER_URL não definida` | Falta o `SERVER_URL` no `.env`. A rodada só prepara a `NEW_MEMORY`. |
| `já existe uma sincronização rodando` | O loop automático já está rodando. É normal. |
| Mudança de outro PC não chegou | O outro PC ainda não enviou. Espere a próxima rodada dele e depois a deste. |
| `aviso: rode 'npm ci' em ...` | Uma skill com `package.json` chegou sem `node_modules`. Rode `npm ci` na pasta indicada. |

Mais casos em [docs/troubleshooting.md](docs/troubleshooting.md).

## Estrutura do projeto

```
src/
  main.py                          rodada completa e modo --loop
  components/
    sync_claude_templates.py       junctions + templates de settings/MCP (caminhos → tokens)
    merge_sharing.py               aplica SHARING_MEMORY na pasta do Claude
    remote_client.py               manifesto, envio/recebimento só do que mudou, assinatura, keygen
docs/                              documentação completa (também é uma skill do Claude)
install_autostart.ps1 / .bat       tarefa agendada do Windows (a cada 15 min)
restart_sync.bat                   reinicia a tarefa depois de atualizar o código
requirements.txt
```

Arquivos locais que **não** vão para o git: `.env`, `NEW_MEMORY/`, `SHARING_MEMORY/`, `.sync_state.json`, `.sync.lock` e `sync.log`.

## Documentação completa e skill para o Claude

A pasta [`docs/`](docs/) tem a documentação detalhada e também é uma **skill do Claude Code** (`claude-memory-sync`). Com ela instalada, o Claude de qualquer PC consegue instalar o projeto, configurar um PC novo, conferir pendências de MCP e plugin e diagnosticar erros.

```powershell
git clone --depth 1 https://github.com/le0nardomartins/memory-interconnection-claude.git $env:TEMP\cms
Copy-Item -Recurse $env:TEMP\cms\docs $HOME\.claude\skills\claude-memory-sync
```

Outra forma é pedir ao próprio Claude: *"instale como skill a pasta docs de https://github.com/le0nardomartins/memory-interconnection-claude"*.

| Documento | Conteúdo |
|---|---|
| [docs/setup.md](docs/setup.md) | Instalação do zero, primeiro PC e PCs seguintes |
| [docs/architecture.md](docs/architecture.md) | Rodada de sync, protocolo, merge, estado local |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Erros, logs e como recuperar |
| [docs/SKILL.md](docs/SKILL.md) | Instruções que o Claude segue quando usa a skill |
